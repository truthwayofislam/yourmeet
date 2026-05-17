import os
import hmac
import hashlib
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from jose import jwt
from database import get_db, row_to_user, USER_COLS
from templating import templates

router = APIRouter()

SECRET = os.getenv("SECRET_KEY", "yourmeet_secret_2024")


def create_token(user_id: int) -> str:
    return jwt.encode({"sub": str(user_id)}, SECRET, algorithm="HS256")


def get_current_user(request: Request, db=Depends(get_db)):
    token = request.cookies.get("token")
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
        cols = ", ".join(USER_COLS)
        row = db.execute(
            f"SELECT {cols} FROM users WHERE id=?", (int(payload["sub"]),)
        ).fetchone()
        return row_to_user(row)
    except Exception:
        return None


def verify_telegram_hash(data: dict) -> bool:
    """Verify Telegram Login Widget / initData hash."""
    bot_token = os.getenv("TELEGRAM_BOTS_KEY", "")
    if not bot_token:
        return False
    received_hash = data.get("hash", "")
    check_data = "\n".join(
        f"{k}={v}" for k, v in sorted(data.items()) if k != "hash"
    )
    secret_key = hmac.new(
        hashlib.sha256(bot_token.encode()).digest(),
        check_data.encode(),
        hashlib.sha256,
    ).hexdigest()
    return secret_key == received_hash


@router.post("/auth/telegram")
async def telegram_auth(request: Request, db=Depends(get_db)):
    """Called from TMA on first open — auto login or create minimal user."""
    body = await request.json()
    tg_id = str(body.get("id", ""))
    name = body.get("first_name", "User")
    lang = body.get("language_code", "en")[:2]

    if not tg_id:
        return JSONResponse({"error": "invalid"}, status_code=400)

    cols = ", ".join(USER_COLS)
    row = db.execute(
        f"SELECT {cols} FROM users WHERE telegram_id=?", (tg_id,)
    ).fetchone()
    user = row_to_user(row)

    if user and user.is_blocked:
        return JSONResponse({"error": "banned"}, status_code=403)

    if user and user.is_rejected:
        # Delete rejected account so user can re-register fresh
        _delete_user_data(db, user.id)
        user = None

    if not user:
        db.execute(
            "INSERT INTO users (name, telegram_id, language) VALUES (?, ?, ?)",
            (name, tg_id, lang),
        )
        db.commit()
        row = db.execute(
            f"SELECT {cols} FROM users WHERE telegram_id=?", (tg_id,)
        ).fetchone()
        user = row_to_user(row)
        new_user = True
    else:
        # Update language if changed
        if lang and lang != user.language:
            db.execute(
                "UPDATE users SET language=? WHERE id=?", (lang, user.id)
            )
            db.commit()
        new_user = not bool(user.photo and user.age and user.gender)

    token = create_token(user.id)
    return JSONResponse({"token": token, "new_user": new_user, "lang": user.language})


@router.get("/logout")
async def logout():
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie("token")
    return resp


def _delete_user_data(db, user_id: int):
    for tbl, c1, c2 in [
        ("likes", "from_user", "to_user"),
        ("matches", "user1_id", "user2_id"),
        ("skips", "user_id", "skipped_id"),
        ("referrals", "referrer_id", "referred_id"),
        ("reports", "reporter_id", "reported_id"),
    ]:
        db.execute(f"DELETE FROM {tbl} WHERE {c1}=? OR {c2}=?", (user_id, user_id))
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
