import os
import hmac
import hashlib
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
