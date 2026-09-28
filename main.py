import os
import re
import hmac
import json
import httpx
import uvicorn
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, Depends, Request, HTTPException
from fastapi.responses import JSONResponse, Response
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

# Telegram file_ids are opaque tokens with no fixed length, but they always
# consist of a single segment of base64url characters. This rejects anything
# that isn't a plausible file_id (path traversal, control chars, SQL, etc.).
_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,200}$")


def _valid_file_id(file_id: str) -> bool:
    return bool(file_id) and bool(_FILE_ID_RE.match(file_id))

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
            drop_pending_updates=True,
        )
        try:
            from telegram import BotCommand
            await admin_bot_app.bot.set_my_commands([
                BotCommand("start", "Admin bot start"),
                BotCommand("pending", "Show pending profiles"),
                BotCommand("pendingall", "Show all users with status"),
                BotCommand("stats", "Full app stats"),
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
        db.execute(
            "UPDATE users SET is_premium=0, super_likes_left=1, daily_swipes=30 "
            "WHERE is_premium=1 AND premium_until != '' AND premium_until < datetime('now')"
        )
        db.commit()
        db.close()
    except Exception as e:
        print(f"[SCHEDULER] expire_premium error: {e}")


app = FastAPI(title="YourMeet", lifespan=lifespan)

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
    db.close()
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
async def proxy_photo(file_id: str, request: Request):
    # Reject anything that isn't a plausible Telegram file_id before it ever
    # reaches the Telegram API (blocks path traversal and injection).
    if not _valid_file_id(file_id):
        raise HTTPException(status_code=400, detail="invalid file_id")

    # Only authenticated users may fetch photos through the proxy.
    db = get_conn()
    try:
        user = await auth.get_current_user(request, db=db)
    finally:
        db.close()
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
    if not BOT_TOKEN or not hmac.compare_digest(token, BOT_TOKEN) or not bot_app:
        return JSONResponse({"error": "invalid"}, status_code=403)
    from telegram import Update
    update = Update.de_json(await request.json(), bot_app.bot)
    await bot_app.process_update(update)
    return JSONResponse({"ok": True})


@app.post("/admin-webhook/{token}")
async def admin_webhook(token: str, request: Request):
    if not ADMIN_BOT_TOKEN or not hmac.compare_digest(token, ADMIN_BOT_TOKEN) or not admin_bot_app:
        return JSONResponse({"error": "invalid"}, status_code=403)
    from telegram import Update
    update = Update.de_json(await request.json(), admin_bot_app.bot)
    await admin_bot_app.process_update(update)
    return JSONResponse({"ok": True})


@app.api_route("/ping", methods=["GET", "HEAD"])
def ping():
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
