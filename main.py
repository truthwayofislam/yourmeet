import os
import re
import time
import hmac
import base64
import hashlib
import json
import httpx
import uvicorn
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, Depends, Request, HTTPException
from fastapi.responses import JSONResponse, Response, HTMLResponse
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

# Telegram file_ids are opaque tokens with no fixed length, but they always
# consist of a single segment of base64url characters. This rejects anything
# that isn't a plausible file_id (path traversal, control chars, SQL, etc.).
_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,200}$")


def _valid_file_id(file_id: str) -> bool:
    return bool(file_id) and bool(_FILE_ID_RE.match(file_id))


def _webhook_secret(token: str) -> str:
    """Stable per-bot secret for Telegram's X-Telegram-Bot-Api-Secret-Token header.

    Telegram echoes this value on every webhook request, so even someone who
    discovers the webhook URL cannot forge updates without the derived secret.
    """
    return hashlib.sha256(("yourmeet-wh:" + token).encode()).hexdigest()

from database import init_db, get_conn, get_db
from routers import auth, profiles, chat, payment, vibe

BOT_TOKEN = os.getenv("TELEGRAM_BOTS_KEY", "")
ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN", "")
APP_URL = os.getenv("APP_URL", "")

bot_app = None
admin_bot_app = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_app, admin_bot_app

    init_db()

    if BOT_TOKEN and APP_URL:
        from bot import build_bot
        bot_app = build_bot()
        await bot_app.initialize()
        await bot_app.bot.set_webhook(
            f"{APP_URL}/webhook/{BOT_TOKEN}",
            secret_token=_webhook_secret(BOT_TOKEN),
            drop_pending_updates=True,
        )
        # Register the command menu so Telegram shows commands in autocomplete.
        try:
            from telegram import BotCommand
            await bot_app.bot.set_my_commands([
                BotCommand("start", "Create or update profile"),
                BotCommand("browse", "Browse & swipe profiles"),
                BotCommand("matches", "See your matches"),
                BotCommand("profile", "View your profile"),
                BotCommand("stats", "Your activity stats"),
                BotCommand("premium", "Upgrade to Premium"),
                BotCommand("likes", "See who liked you (Premium)"),
                BotCommand("mystery", "Mystery Mode (Premium)"),
                BotCommand("share", "Invite friends"),
                BotCommand("language", "Change language"),
                BotCommand("boost", "Boost your profile (Premium)"),
                BotCommand("block", "Block a user"),
                BotCommand("filters", "Set age/distance filters"),
                BotCommand("editprofile", "Edit your profile"),
                BotCommand("delete", "Delete your account"),
                BotCommand("help", "Show all commands"),
            ])
        except Exception as e:
            print(f"[BOT] set_my_commands failed: {e}")
        await bot_app.start()
        print("[BOT] Webhook set")
    else:
        print("[BOT] Skipped — BOT_TOKEN or APP_URL missing")

    if ADMIN_BOT_TOKEN and APP_URL:
        from admin_bot import build_admin_bot
        admin_bot_app = build_admin_bot()
        await admin_bot_app.initialize()
        await admin_bot_app.bot.set_webhook(
            f"{APP_URL}/admin-webhook/{ADMIN_BOT_TOKEN}",
            secret_token=_webhook_secret(ADMIN_BOT_TOKEN),
            drop_pending_updates=True,
        )
        try:
            from telegram import BotCommand
            await admin_bot_app.bot.set_my_commands([
                BotCommand("start", "Admin bot start"),
                BotCommand("pending", "Show pending profiles"),
                BotCommand("pendingall", "Show all users with status"),
                BotCommand("stats", "Full app stats"),
                BotCommand("reports", "Show recent user reports"),
                BotCommand("post", "Generate promo post (lang)"),
                BotCommand("broadcast", "Send to all users"),
                BotCommand("remind", "Remind incomplete users"),
                BotCommand("remind_blocked", "Notify rejected users"),
                BotCommand("find", "Search user by name"),
                BotCommand("user", "View user details"),
                BotCommand("users", "List all users"),
                BotCommand("cleanup", "Find incomplete users"),
                BotCommand("confirmcleanup", "Delete incomplete users"),
                BotCommand("deleteuser", "Delete a user"),
                BotCommand("fixuser", "Reset a user to pending"),
                BotCommand("grantpremium", "Gift premium days (support)"),
                BotCommand("revokepremium", "Revoke premium (support)"),
                BotCommand("auditlog", "Show recent admin actions"),
            ])
        except Exception as e:
            print(f"[ADMIN BOT] set_my_commands failed: {e}")
        await admin_bot_app.start()
        print("[ADMIN BOT] Webhook set")
    else:
        print("[ADMIN BOT] Skipped — ADMIN_BOT_TOKEN or APP_URL missing")

    scheduler = AsyncIOScheduler()
    scheduler.add_job(_cleanup_chats, "interval", minutes=1, misfire_grace_time=120, max_instances=1)
    scheduler.add_job(_notify_missed_chats, "interval", minutes=1, misfire_grace_time=120, max_instances=1)
    scheduler.add_job(_expire_premium, "interval", hours=1, misfire_grace_time=600, max_instances=1)
    scheduler.start()
    print("[SCHEDULER] Started")

    yield

    if bot_app:
        await bot_app.stop()
        await bot_app.shutdown()
    if admin_bot_app:
        await admin_bot_app.stop()
        await admin_bot_app.shutdown()
    scheduler.shutdown()


async def _cleanup_chats():
    try:
        from routers.chat import cleanup_expired_sessions
        db = get_conn()
        await cleanup_expired_sessions(db)
        db.close()
    except Exception as e:
        print(f"[SCHEDULER] cleanup_chats error: {e}")


async def _notify_missed_chats():
    try:
        from routers.chat import notify_missed_chats
        db = get_conn()
        await notify_missed_chats(db)
        db.close()
    except Exception as e:
        print(f"[SCHEDULER] notify_missed_chats error: {e}")


async def _expire_premium():
    try:
        db = get_conn()
        rows = db.execute(
            "SELECT telegram_id FROM users "
            "WHERE is_premium=1 AND premium_until != '' AND premium_until < datetime('now')"
        ).fetchall()
        db.execute(
            "UPDATE users SET is_premium=0, super_likes_left=1, daily_swipes=30 "
            "WHERE is_premium=1 AND premium_until != '' AND premium_until < datetime('now')"
        )
        db.commit()
        db.close()
        if not rows:
            return
        # Tell users their premium expired — silence reads like a billing bug.
        token = os.getenv("TELEGRAM_BOTS_KEY", "").strip().strip("'\"")
        if not token:
            return
        import httpx
        for (tg_id,) in rows:
            if not tg_id:
                continue
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={
                            "chat_id": tg_id,
                            "text": "⏰ <b>Your Premium has expired.</b>\n\n"
                                    "You're back on the free plan (30 swipes/day).\n"
                                    "Renew anytime with /premium — unlimited swipes & chat are waiting! 👑",
                            "parse_mode": "HTML",
                        },
                    )
            except Exception:
                pass
    except Exception as e:
        print(f"[SCHEDULER] expire_premium error: {e}")


# docs/redoc/openapi disabled: FastAPI exposes them publicly by default, which
# hands outsiders a complete map of every endpoint (mini app era is over, no
# one needs the schema).
app = FastAPI(
    title="YourMeet",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Basic hardening headers — the terms/privacy pages must not be framable."""
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response

app.include_router(auth.router)
app.include_router(profiles.router)
app.include_router(chat.router)
app.include_router(payment.router)
app.include_router(vibe.router)


@app.get("/api/export")
async def export_data(request: Request, db=Depends(get_db)):
    """Export all of the current user's data (GDPR-style data portability)."""
    user = await auth.get_current_user(request, db=db)
    if not user:
        raise HTTPException(status_code=401, detail="unauthorized")
    cols = ", ".join(auth.USER_COLS)
    row = db.execute(f"SELECT {cols} FROM users WHERE id=?", (user.id,)).fetchone()
    u = auth.row_to_user(row)
    profile = {
        "id": u.id, "name": u.name, "age": u.age, "gender": u.gender,
        "interested_in": getattr(u, "interested_in", "both"),
        "bio": getattr(u, "bio", ""), "city": getattr(u, "city", ""),
        "photos": json.loads(getattr(u, "photos", "[]") or "[]"),
        "interests": json.loads(getattr(u, "interests", "[]") or "[]"),
        "social_handle": getattr(u, "social_handle", ""),
        "language": getattr(u, "language", "en"),
        "is_verified": u.is_verified, "is_premium": u.is_premium,
        "profile_views": getattr(u, "profile_views", 0),
        "created_at": getattr(u, "created_at", ""),
    }
    likes_given = [r[0] for r in db.execute("SELECT to_user FROM likes WHERE from_user=?", (user.id,)).fetchall()]
    likes_received = [r[0] for r in db.execute("SELECT from_user FROM likes WHERE to_user=?", (user.id,)).fetchall()]
    matches = [r[0] for r in db.execute("SELECT id FROM matches WHERE user1_id=? OR user2_id=?", (user.id, user.id)).fetchall()]
    reports = [r[0] for r in db.execute("SELECT reported_id FROM reports WHERE reporter_id=?", (user.id,)).fetchall()]
    blocks = [r[0] for r in db.execute("SELECT blocked_id FROM user_blocks WHERE blocker_id=?", (user.id,)).fetchall()]
    return JSONResponse({
        "profile": profile,
        "likes_given_to": likes_given,
        "likes_received_from": likes_received,
        "match_ids": matches,
        "reported_users": reports,
        "blocked_users": blocks,
        "exported_at": datetime.utcnow().isoformat(),
    })


@app.get("/photo/{file_id:path}")
async def proxy_photo(file_id: str, request: Request, db=Depends(get_db)):
    # Reject anything that isn't a plausible Telegram file_id before it ever
    # reaches the Telegram API (blocks path traversal and injection).
    if not _valid_file_id(file_id):
        raise HTTPException(status_code=400, detail="invalid file_id")

    # Only authenticated users may fetch photos through the proxy.
    user = await auth.get_current_user(request, db=db)
    if not user:
        raise HTTPException(status_code=401, detail="unauthorized")

    token = os.getenv("TELEGRAM_BOTS_KEY", "").strip().strip("'\"")
    if not token:
        return Response(status_code=404)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}")
            if r.is_success and r.json().get("ok"):
                file_path = r.json()["result"]["file_path"]
                img = await client.get(f"https://api.telegram.org/file/bot{token}/{file_path}")
                return Response(content=img.content, media_type="image/jpeg")
    except Exception as e:
        print(f"[PHOTO] proxy error: {e}")
    return Response(status_code=404)


@app.post("/webhook/{token}")
async def webhook(token: str, request: Request):
    if (
        not BOT_TOKEN
        or not hmac.compare_digest(token, BOT_TOKEN)
        or not bot_app
        or not hmac.compare_digest(
            request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""),
            _webhook_secret(BOT_TOKEN),
        )
    ):
        return JSONResponse({"error": "invalid"}, status_code=403)
    from telegram import Update
    update = Update.de_json(await request.json(), bot_app.bot)
    await bot_app.process_update(update)
    return JSONResponse({"ok": True})


@app.post("/admin-webhook/{token}")
async def admin_webhook(token: str, request: Request):
    if (
        not ADMIN_BOT_TOKEN
        or not hmac.compare_digest(token, ADMIN_BOT_TOKEN)
        or not admin_bot_app
        or not hmac.compare_digest(
            request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""),
            _webhook_secret(ADMIN_BOT_TOKEN),
        )
    ):
        return JSONResponse({"error": "invalid"}, status_code=403)
    from telegram import Update
    update = Update.de_json(await request.json(), admin_bot_app.bot)
    await admin_bot_app.process_update(update)
    return JSONResponse({"ok": True})


@app.api_route("/ping", methods=["GET", "HEAD"])
def ping():
    return JSONResponse({"status": "ok"})


# ── Public web profiles (/p/<id>) ────────────────────────────────────────────
# Bot buttons link here. Links are HMAC-signed and expire in 1 hour, so a
# leaked URL goes stale and outsiders can't enumerate profiles by id.

_PROFILE_LINK_TTL = 3600


def profile_sig(viewer_id: int, target_id: int, exp: int) -> str:
    secret = os.getenv("SECRET_KEY", "")
    msg = f"{viewer_id}:{target_id}:{exp}"
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def make_profile_link(viewer_id: int, target_id: int) -> str:
    """Signed, time-limited /p/ URL — also imported by bot.py for buttons."""
    exp = int(time.time()) + _PROFILE_LINK_TTL
    return f"{APP_URL}/p/{target_id}?v={viewer_id}&e={exp}&t={profile_sig(viewer_id, target_id, exp)}"


_PHOTO_CACHE: dict = {}


async def _photo_b64(file_id: str) -> str:
    """Download a profile photo via the MAIN bot's file_id, base64 for embed."""
    now = time.time()
    cached = _PHOTO_CACHE.get(file_id)
    if cached and now - cached[1] < 600:
        return cached[0]
    token = os.getenv("TELEGRAM_BOTS_KEY", "").strip().strip("'\"")
    if not token or not _valid_file_id(file_id):
        return ""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}")
            if not (r.is_success and r.json().get("ok")):
                return ""
            fp = r.json()["result"]["file_path"]
            img = await client.get(f"https://api.telegram.org/file/bot{token}/{fp}")
            if not img.is_success:
                return ""
            b64 = base64.b64encode(img.content).decode()
        if len(_PHOTO_CACHE) > 64:
            _PHOTO_CACHE.clear()
        _PHOTO_CACHE[file_id] = (b64, now)
        return b64
    except Exception:
        return ""


_PROFILE_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__NAME__ 💕 YourMeet</title>
<meta property="og:title" content="__NAME__ on YourMeet 💕">
<meta name="theme-color" content="#ff4d6d">
<style>
/* Reset + base */
* {margin:0;padding:0;box-sizing:border-box}

/* Body + center layout */
body {
    min-height:100vh;
    background:linear-gradient(160deg,#1a0b2e,#2d1b4e 50%,#4a1942);
    font-family:-apple-system,'Segoe UI',Roboto,sans-serif;
    color:#fff;
    padding:20px;
    display:flex;
    align-items:center;
    justify-content:center;
    padding-top:40px;
    padding-bottom:40px;
}

/* Card with enhanced shadow and border */
.card {
    max-width:430px;
    width:100%;
    background:rgba(255,255,255,.07);
    backdrop-filter:blur(12px);
    border:1px solid rgba(255,255,255,.18);
    border-radius:28px;
    overflow:hidden;
    margin-top:24px;
    margin-bottom:24px;
    box-shadow:0 20px 60px rgba(0,0,0,.45), inset 0 0 0 1px rgba(255,255,255,.12);
}

/* Photo area with better mystery mode */
.photo {
    width:100%;
    aspect-ratio:4/5;
    object-fit:cover;
    display:block;
    background:#241238;
    transition:transform .3s ease;
}
.card:hover .photo {transform:scale(1.02)}

.nophoto {
    width:100%;
    aspect-ratio:4/5;
    display:flex;
    align-items:center;
    justify-content:center;
    font-size:80px;
    background:linear-gradient(160deg,#3b1d5e,#57264b);
    border-bottom:1px solid rgba(255,255,255,.08);
    position:relative;
}

/* Profile content */
.pad {
    padding:28px 24px;
    position:relative;
}

/* Name + age */
h1 {
    font-size:26px;
    font-weight:700;
    display:flex;
    align-items:center;
    gap:10px;
    flex-wrap:wrap;
    margin-bottom:14px;
}
.age {
    font-weight:400;
    opacity:.85;
    font-size:20px;
    color:#f8bbd0;
}

/* Badges with hover effect */
.badges {
    display:flex;
    gap:10px;
    flex-wrap:wrap;
    margin-bottom:18px;
}
.badge {
    font-size:12px;
    background:rgba(255,255,255,.14);
    border:1px solid rgba(255,255,255,.25);
    border-radius:999px;
    padding:7px 14px;
    color:#fff;
    font-weight:600;
    transition:all .2s ease;
    cursor:default;
}
.badge:hover {
    background:rgba(255,255,255,.22);
    transform:translateY(-2px);
}

/* Meta info (city, gender, looking_for) */
.meta {
    margin-top:14px;
    font-size:15px;
    color:#e8d9ff;
    line-height:1.7;
    display:flex;
    flex-wrap:wrap;
    gap:10px 16px;
}

/* Bio section with better styling */
.bio {
    margin-top:18px;
    font-size:16px;
    line-height:1.7;
    color:#f3eaff;
    background:linear-gradient(135deg, rgba(0,0,0,.28), rgba(255,255,255,.06));
    border-radius:18px;
    padding:18px 20px;
    white-space:pre-wrap;
    border:1px solid rgba(255,255,255,.12);
    position:relative;
    box-shadow:inset 0 2px 8px rgba(0,0,0,.3);
}

/* Tags with hover */
.tags {
    display:flex;
    gap:10px;
    flex-wrap:wrap;
    margin-top:18px;
}
.tag {
    font-size:13px;
    background:linear-gradient(90deg, rgba(255,77,109,.22), rgba(255,135,180,.15));
    border:1px solid rgba(255,77,109,.35);
    padding:8px 16px;
    border-radius:999px;
    color:#ffd6df;
    font-weight:600;
    transition:all .2s ease;
    cursor:pointer;
}
.tag:hover {
    background:rgba(255,77,109,.35);
    transform:translateY(-1px);
    box-shadow:0 4px 12px rgba(255,77,109,.25);
}

/* Footer with gradient */
.ft {
    text-align:center;
    font-size:12px;
    color:#b9a7d9;
    padding:16px 14px;
    background:rgba(0,0,0,.35);
    border-top:1px solid rgba(255,255,255,.12);
    backdrop-filter:blur(8px);
}

/* Mobile optimization */
@media (max-width:480px) {
    body {padding:12px;}
    .card {border-radius:24px;}
    h1 {font-size:24px;}
    .bio {font-size:15px; padding:16px 18px;}
}

/* Subtle animations */
@keyframes fadeInUp {
    from {opacity:0;transform:translateY(20px)}
    to {opacity:1;transform:translateY(0)}
}
.card {
    animation:fadeInUp .4s ease-out .1s both;
}
</style>
</head>
<body><div class="card">
__PHOTO__
<div class="pad">
<h1>__NAME__ <span class="age">__AGE__</span></h1>
<div class="badges">__BADGES__</div>
<div class="meta">__META__</div>
__BIO__
__TAGS__
</div>
<div class="ft">💕 YourMeet — find your match on Telegram</div>
</div></body></html>"""


@app.get("/p/{target_id}")
async def profile_page(target_id: int, v: int = 0, e: int = 0, t: str = ""):
    # Signature + expiry gate — unsigned links must never reveal profiles.
    if not hmac.compare_digest(t, profile_sig(v, target_id, e)) or e < int(time.time()):
        raise HTTPException(status_code=404)

    from database import row_to_user, USER_COLS
    from textsafe import esc
    db = get_conn()
    cols = ", ".join(USER_COLS)
    row = db.execute(f"SELECT {cols} FROM users WHERE id=?", (target_id,)).fetchone()
    db.close()
    u = row_to_user(row)
    if not u or u.is_blocked:
        raise HTTPException(status_code=404)
    # Others' profiles are visible only while approved; your own always works.
    if v != target_id and not u.is_approved:
        raise HTTPException(status_code=404)

    from datetime import datetime as _dt
    mystery = False
    try:
        mu = getattr(u, "mystery_until", "") or ""
        mystery = bool(mu) and _dt.strptime(mu, "%Y-%m-%d %H:%M:%S") > _dt.utcnow()
    except ValueError:
        pass

    photo_html = '<div class="nophoto">🙈</div>'
    if mystery:
        photo_html = '<div class="nophoto">🤫</div>'
    elif getattr(u, "photo", ""):
        b64 = await _photo_b64(u.photo)
        if b64:
            photo_html = f'<img class="photo" src="data:image/jpeg;base64,{b64}" alt="profile photo">'

    badges = []
    if u.is_verified:
        badges.append('<span class="badge">✅ Verified</span>')
    if u.is_premium:
        badges.append('<span class="badge">👑 Premium</span>')
    try:
        bu = getattr(u, "boosted_until", "") or ""
        if bu and _dt.strptime(bu, "%Y-%m-%d %H:%M:%S") > _dt.utcnow():
            badges.append('<span class="badge">🚀 Boosted</span>')
    except ValueError:
        pass

    lf = {"dating": "💚 Dating", "sexting": "🔥 Fun", "relationship": "🤝 Relationship", "any": "❓ Open"}.get(
        getattr(u, "looking_for", "") or "", ""
    )
    meta_parts = []
    if getattr(u, "city", ""):
        meta_parts.append(f"📍 {esc(u.city)}")
    if u.gender:
        meta_parts.append("👤 " + ("👨" if u.gender == "male" else "👩" if u.gender == "female" else esc(u.gender)))
    if lf:
        meta_parts.append(esc(lf) if not lf.startswith(("💚", "🔥", "🤝", "❓")) else lf)

    try:
        interests = json.loads(getattr(u, "interests", "[]") or "[]")[:8]
    except Exception:
        interests = []
    tags_html = (
        '<div class="tags">' + "".join(f'<span class="tag">#{esc(i)}</span>' for i in interests) + "</div>"
        if interests else ""
    )
    bio = getattr(u, "bio", "") or ""
    bio_html = f'<div class="bio">{esc(bio)}</div>' if bio else ""

    name = esc(u.name or "Someone")
    page = (
        _PROFILE_PAGE
        .replace("__PHOTO__", photo_html)
        .replace("__NAME__", name)
        .replace("__AGE__", str(u.age or ""))
        .replace("__BADGES__", "".join(badges))
        .replace("__META__", " · ".join(meta_parts))
        .replace("__BIO__", bio_html)
        .replace("__TAGS__", tags_html)
    )
    return HTMLResponse(page)


@app.get("/terms", response_class=HTMLResponse)
def terms():
    with open("static/terms.html", encoding="utf-8") as f:
        return f.read()


@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    with open("static/privacy.html", encoding="utf-8") as f:
        return f.read()


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
