import os
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from database import get_db, USER_COLS
from routers.auth import get_current_user

router = APIRouter()

_COLS = ", ".join(USER_COLS)
BOT_TOKEN = os.getenv("TELEGRAM_BOTS_KEY", "")


@router.post("/chat/start/{match_id}")
async def start_chat(match_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    """Start a 1-min (free) or unlimited (premium) bot chat session on match."""
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    # Verify match exists
    match = db.execute(
        "SELECT user1_id, user2_id FROM matches WHERE id=? AND (user1_id=? OR user2_id=?)",
        (match_id, current_user.id, current_user.id),
    ).fetchone()
    if not match:
        return JSONResponse({"error": "match_not_found"}, status_code=404)

    other_id = match[1] if match[0] == current_user.id else match[0]
    other = db.execute(f"SELECT {_COLS} FROM users WHERE id=?", (other_id,)).fetchone()
    if not other:
        return JSONResponse({"error": "user_not_found"}, status_code=404)

    from database import row_to_user
    other_user = row_to_user(other)

    if not current_user.telegram_id or not other_user.telegram_id:
        return JSONResponse({"error": "telegram_required"}, status_code=400)

    # Check if active session already exists
    existing = db.execute(
        """SELECT id FROM chat_sessions
           WHERE ((user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?))
           AND is_active=1""",
        (current_user.id, other_id, other_id, current_user.id),
    ).fetchone()
    if existing:
        return JSONResponse({"ok": True, "session_id": existing[0], "already_active": True})

    # Set expiry — 1 min for free, None for premium
    is_premium_chat = current_user.is_premium or other_user.is_premium
    if is_premium_chat:
        expires_at = None
    else:
        expires_at = (datetime.utcnow() + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")

    db.execute(
        """INSERT INTO chat_sessions
           (user1_id, user2_id, user1_tg_id, user2_tg_id, is_premium_chat, expires_at)
           VALUES (?,?,?,?,?,?)""",
        (current_user.id, other_id,
         current_user.telegram_id, other_user.telegram_id,
         int(is_premium_chat), expires_at),
    )
    db.commit()
    session_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Notify both users via bot
    duration = "unlimited" if is_premium_chat else "1 minute"
    await _notify_chat_start(
        current_user.telegram_id, other_user.name, duration, other_user.language or "en"
    )
    await _notify_chat_start(
        other_user.telegram_id, current_user.name, duration, current_user.language or "en"
    )

    return JSONResponse({"ok": True, "session_id": session_id, "expires_at": expires_at})


@router.post("/chat/end/{session_id}")
async def end_chat(session_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    session = db.execute(
        "SELECT user1_tg_id, user2_tg_id FROM chat_sessions WHERE id=? AND is_active=1",
        (session_id,),
    ).fetchone()
    if not session:
        return JSONResponse({"ok": True})

    db.execute("UPDATE chat_sessions SET is_active=0 WHERE id=?", (session_id,))
    db.commit()

    await _notify_chat_end(session[0])
    await _notify_chat_end(session[1])

    return JSONResponse({"ok": True})


async def forward_message(tg_id_from: str, text: str, db) -> bool:
    """Called by bot when user sends message — forward to other user in active session."""
    session = db.execute(
        """SELECT id, user1_tg_id, user2_tg_id, expires_at, is_premium_chat
           FROM chat_sessions
           WHERE (user1_tg_id=? OR user2_tg_id=?) AND is_active=1
           ORDER BY created_at DESC LIMIT 1""",
        (tg_id_from, tg_id_from),
    ).fetchone()

    if not session:
        return False

    session_id, tg1, tg2, expires_at, is_premium = session

    # Check expiry
    if not is_premium and expires_at:
        if datetime.utcnow() > datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S"):
            db.execute("UPDATE chat_sessions SET is_active=0 WHERE id=?", (session_id,))
            db.commit()
            await _notify_chat_end(tg_id_from)
            return False

    target_tg_id = tg2 if tg1 == tg_id_from else tg1

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": target_tg_id,
                    "text": f"💬 {text}",
                    "parse_mode": "Markdown",
                },
            )
        return True
    except Exception as e:
        print(f"[CHAT] forward failed: {e}")
        return False


async def _start_chat_session(db, match_id: int, user1, user2):
    """Auto-start chat session on match."""
    if not user1.telegram_id or not user2.telegram_id:
        return
    existing = db.execute(
        """SELECT id FROM chat_sessions
           WHERE ((user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?))
           AND is_active=1""",
        (user1.id, user2.id, user2.id, user1.id),
    ).fetchone()
    if existing:
        return
    is_premium_chat = user1.is_premium or user2.is_premium
    expires_at = None if is_premium_chat else \
        (datetime.utcnow() + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        """INSERT INTO chat_sessions
           (user1_id, user2_id, user1_tg_id, user2_tg_id, is_premium_chat, expires_at)
           VALUES (?,?,?,?,?,?)""",
        (user1.id, user2.id, user1.telegram_id, user2.telegram_id,
         int(is_premium_chat), expires_at),
    )
    db.commit()
    duration = "unlimited" if is_premium_chat else "1 minute"
    await _notify_chat_start(user1.telegram_id, user2.name, duration, user1.language or "en")
    await _notify_chat_start(user2.telegram_id, user1.name, duration, user2.language or "en")


async def cleanup_expired_sessions(db):
    """Called by scheduler every minute."""
    expired = db.execute(
        "SELECT id, user1_tg_id, user2_tg_id FROM chat_sessions WHERE is_active=1 AND expires_at IS NOT NULL AND expires_at < datetime('now')"
    ).fetchall()
    for session_id, tg1, tg2 in expired:
        db.execute("UPDATE chat_sessions SET is_active=0 WHERE id=?", (session_id,))
        await _notify_chat_end(tg1)
        await _notify_chat_end(tg2)
    if expired:
        db.commit()


async def _notify_chat_start(tg_id: str, other_name: str, duration: str, lang: str):
    if not tg_id or not BOT_TOKEN:
        return
    text = f"💬 *Chat started with {other_name}!*\n\n⏱ Duration: *{duration}*\n\nSend your messages here — they'll be forwarded directly."
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": tg_id, "text": text, "parse_mode": "Markdown"},
            )
    except Exception:
        pass


async def _notify_chat_end(tg_id: str):
    if not tg_id or not BOT_TOKEN:
        return
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": tg_id,
                    "text": "⏰ *Chat session ended.*\n\nUpgrade to Premium for unlimited chat! 👑",
                    "parse_mode": "Markdown",
                },
            )
    except Exception:
        pass
