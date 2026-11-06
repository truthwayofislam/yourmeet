import os
import hmac
import hashlib
import time
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from jose import jwt
from database import get_db, row_to_user, USER_COLS

router = APIRouter()

SECRET = os.getenv("SECRET_KEY", "")
if not SECRET:
    raise RuntimeError("SECRET_KEY env var is not set!")


def create_token(user_id: int) -> str:
    from datetime import datetime, timedelta
    exp = datetime.utcnow() + timedelta(days=30)
    # NOTE: is_premium is deliberately NOT embedded in the token. The server
    # always re-verifies premium status against the database on every request,
    # so a forged or stale token can never grant premium access.
    return jwt.encode({"sub": str(user_id), "exp": exp}, SECRET, algorithm="HS256")


def _valid_telegram_login(data: dict, bot_token: str) -> bool:
    supplied_hash = data.get("hash")
    if not isinstance(supplied_hash, str):
        return False

    try:
        auth_date = int(data.get("auth_date", ""))
    except (TypeError, ValueError):
        return False
    now = int(time.time())
    if auth_date > now + 60 or now - auth_date > 86400:
        return False

    data_check_string = "\n".join(
        f"{key}={value}"
        for key, value in sorted(data.items())
        if key != "hash" and isinstance(value, (str, int, float))
    )
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    expected_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(supplied_hash, expected_hash)


async def get_current_user(request: Request, db=Depends(get_db)):
    token = request.cookies.get("token") or request.headers.get("X-Token")
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
        sub = payload.get("sub")
        if sub is None:
            return None
        user_id = int(sub)
    except (ValueError, TypeError, KeyError, jwt.JWTError):
        return None
    try:
        cols = ", ".join(USER_COLS)
        row = db.execute(
            "SELECT " + cols + " FROM users WHERE id=?", (user_id,)
        ).fetchone()
        user = row_to_user(row)
        # Banned users must be locked out of the API immediately, even though
        # their JWT is still valid for up to 30 days.
        if user and user.is_blocked:
            return None
        return user
    except Exception:
        return None


@router.post("/api/auth/telegram")
async def telegram_login(request: Request, db=Depends(get_db)):
    """Verify Telegram Login Widget data and issue the app's normal session cookie."""
    bot_token = os.getenv("TELEGRAM_BOTS_KEY", "").strip().strip("'\"")
    if not bot_token:
        return JSONResponse({"error": "telegram_login_unavailable"}, status_code=503)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_payload"}, status_code=400)
    if not isinstance(data, dict) or not _valid_telegram_login(data, bot_token):
        return JSONResponse({"error": "invalid_telegram_auth"}, status_code=401)

    try:
        telegram_id = str(int(data.get("id", "")))
    except (TypeError, ValueError):
        return JSONResponse({"error": "invalid_telegram_auth"}, status_code=401)
    if int(telegram_id) <= 0:
        return JSONResponse({"error": "invalid_telegram_auth"}, status_code=401)

    row = db.execute(
        f"SELECT {', '.join(USER_COLS)} FROM users WHERE telegram_id=?",
        (telegram_id,),
    ).fetchone()
    user = row_to_user(row)
    if user and user.is_blocked:
        return JSONResponse({"error": "account_unavailable"}, status_code=403)

    if not user:
        first_name = str(data.get("first_name", "")).strip()
        last_name = str(data.get("last_name", "")).strip()
        name = f"{first_name} {last_name}".strip()[:50] or "New member"
        language = str(data.get("language_code", "en"))[:8]
        db.execute(
            "INSERT OR IGNORE INTO users (name, telegram_id, language) VALUES (?,?,?)",
            (name, telegram_id, language),
        )
        db.commit()
        row = db.execute(
            f"SELECT {', '.join(USER_COLS)} FROM users WHERE telegram_id=?",
            (telegram_id,),
        ).fetchone()
        user = row_to_user(row)

    if not user:
        return JSONResponse({"error": "account_unavailable"}, status_code=503)

    response = JSONResponse({"ok": True})
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    secure_cookie = request.url.scheme == "https" or forwarded_proto == "https"
    response.set_cookie(
        "token",
        create_token(user.id),
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/api/auth/me")
async def current_user_profile(request: Request, db=Depends(get_db)):
    user = await get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse({
        "id": user.id,
        "profile": {
            "name": user.name or "",
            "age": user.age or "",
            "gender": user.gender or "",
            "interested_in": getattr(user, "interested_in", "both") or "both",
            "city": user.city or "",
            "bio": user.bio or "",
            "social_handle": getattr(user, "social_handle", "") or "",
            "marital_intent": getattr(user, "marital_intent", "serious_marriage") or "serious_marriage",
            "deeni_level": getattr(user, "deeni_level", "") or "",
            "sect": getattr(user, "sect", "") or "",
            "family_values": getattr(user, "family_values", "") or "",
            "guardian_consented": bool(getattr(user, "guardian_consented", 0)),
            "has_photo": bool(getattr(user, "photo", "")),
        },
        "status": {
            "approved": bool(user.is_approved),
            "rejected": bool(user.is_rejected),
            "terms_accepted": bool(getattr(user, "terms_accepted", 0)),
            "submitted": bool(
                user.age
                and user.gender
                and user.city
                and user.bio
                and getattr(user, "photo", "")
            ),
        },
    })


@router.post("/api/auth/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie("token", path="/", httponly=True, samesite="lax")
    return response


def _delete_user_data(db, user_id: int):
    for tbl, c1, c2 in [
        ("likes", "from_user", "to_user"),
        ("matches", "user1_id", "user2_id"),
        ("skips", "user_id", "skipped_id"),
        ("referrals", "referrer_id", "referred_id"),
        ("reports", "reporter_id", "reported_id"),
    ]:
        db.execute(f"DELETE FROM {tbl} WHERE {c1}=? OR {c2}=?", (user_id, user_id))
    session_ids = [
        r[0] for r in db.execute(
            "SELECT id FROM chat_sessions WHERE user1_id=? OR user2_id=?", (user_id, user_id)
        ).fetchall()
    ]
    if session_ids:
        placeholders = ",".join("?" * len(session_ids))
        db.execute(f"DELETE FROM chat_messages WHERE session_id IN ({placeholders})", tuple(session_ids))
    db.execute("DELETE FROM chat_sessions WHERE user1_id=? OR user2_id=?", (user_id, user_id))
    db.execute("DELETE FROM vibe_answers WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
