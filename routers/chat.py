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
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
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

        existing = db.execute(
            """SELECT id FROM chat_sessions
               WHERE ((user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?))
               AND is_active=1""",
            (current_user.id, other_id, other_id, current_user.id),
        ).fetchone()
        if existing:
            return JSONResponse({"ok": True, "session_id": existing[0], "already_active": True})

        is_premium_chat = current_user.is_premium or other_user.is_premium
        expires_at = None if is_premium_chat else \
            (datetime.utcnow() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")

        db.execute(
            """INSERT INTO chat_sessions
               (user1_id, user2_id, user1_tg_id, user2_tg_id, is_premium_chat, expires_at)
               VALUES (?,?,?,?,?,?)""",
            (current_user.id, other_id, current_user.telegram_id, other_user.telegram_id,
             int(is_premium_chat), expires_at),
        )
        db.commit()
        session_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    except Exception as e:
        print(f"[CHAT START] error: {e}")
        return JSONResponse({"error": "Something went wrong."}, status_code=500)

    duration = "unlimited" if is_premium_chat else "5 minutes"
    await _notify_chat_start(current_user.telegram_id, other_user.name, duration, other_user.language or "en")
    await _notify_chat_start(other_user.telegram_id, current_user.name, duration, current_user.language or "en")
    return JSONResponse({"ok": True, "session_id": session_id, "expires_at": expires_at})


@router.get("/api/chat/history/{match_id}")
async def chat_history(match_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    """Premium only — get chat message history for a match."""
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not current_user.is_premium:
        return JSONResponse({"error": "premium_required"}, status_code=403)
    try:
        # Verify match belongs to user
        match = db.execute(
            "SELECT user1_id, user2_id FROM matches WHERE id=? AND (user1_id=? OR user2_id=?)",
            (match_id, current_user.id, current_user.id),
        ).fetchone()
        if not match:
            return JSONResponse({"error": "match_not_found"}, status_code=404)

        other_id = match[1] if match[0] == current_user.id else match[0]
        other = db.execute("SELECT name, telegram_id, social_handle FROM users WHERE id=?", (other_id,)).fetchone()
        other_name = other[0] if other else "Unknown"
        other_social = other[2] if other else ""

        # Get all sessions for this match
        sessions = db.execute(
            """SELECT id FROM chat_sessions
               WHERE (user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?)
               ORDER BY created_at DESC""",
            (current_user.id, other_id, other_id, current_user.id),
        ).fetchall()

        if not sessions:
            return JSONResponse({"messages": [], "other_name": other_name, "other_social": other_social})

        session_ids = [s[0] for s in sessions]
        placeholders = ",".join("?" * len(session_ids))

        messages = db.execute(
            f"""SELECT sender_tg_id, message, created_at
                FROM chat_messages
                WHERE session_id IN ({placeholders})
                ORDER BY created_at ASC""",
            tuple(session_ids),
        ).fetchall()

        result = []
        for sender_tg_id, message, created_at in messages:
            is_me = sender_tg_id == current_user.telegram_id
            result.append({
                "text": message,
                "is_me": is_me,
                "sender": "You" if is_me else other_name,
                "time": (created_at or "")[:16].replace("T", " "),
            })

        return JSONResponse({"messages": result, "other_name": other_name, "other_social": other_social})
    except Exception as e:
        print(f"[CHAT HISTORY] error: {e}")
        return JSONResponse({"messages": [], "other_name": ""})


@router.post("/chat/end/{session_id}")
async def end_chat(session_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        session = db.execute(
            "SELECT user1_tg_id, user2_tg_id FROM chat_sessions WHERE id=? AND is_active=1",
            (session_id,),
        ).fetchone()
        if not session:
            return JSONResponse({"ok": True})
        db.execute("UPDATE chat_sessions SET is_active=0 WHERE id=?", (session_id,))
        db.commit()
    except Exception as e:
        print(f"[CHAT END] error: {e}")
        return JSONResponse({"error": "end_failed"}, status_code=500)
    await _notify_chat_end(session[0])
    await _notify_chat_end(session[1])
    return JSONResponse({"ok": True})


async def forward_message(tg_id_from: str, text: str, db) -> bool:
    """Called by bot when user sends message — forward to other user in active session."""
    # Sanitize message
    if not text or not text.strip():
        return False
    text = text.strip()[:1000]  # Max 1000 chars
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
            # Notify BOTH users that chat ended
            await _notify_chat_end(tg1)
            await _notify_chat_end(tg2)
            return False

    target_tg_id = tg2 if tg1 == tg_id_from else tg1

    token = os.getenv("TELEGRAM_BOTS_KEY", "")
    if not token:
        print("[CHAT] BOT_TOKEN missing, cannot forward message")
        return False

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": target_tg_id,
                    "text": f"💬 {text}",
                    "parse_mode": "Markdown",
                },
            )
        # Save message for premium history
        try:
            db.execute(
                "INSERT INTO chat_messages (session_id, sender_tg_id, message) VALUES (?,?,?)",
                (session_id, tg_id_from, text),
            )
            db.commit()
        except Exception as e:
            print(f"[CHAT] save message failed: {e}")
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
        (datetime.utcnow() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        """INSERT INTO chat_sessions
           (user1_id, user2_id, user1_tg_id, user2_tg_id, is_premium_chat, expires_at)
           VALUES (?,?,?,?,?,?)""",
        (user1.id, user2.id, user1.telegram_id, user2.telegram_id,
         int(is_premium_chat), expires_at),
    )
    db.commit()
    duration = "unlimited" if is_premium_chat else "5 minutes"
    await _notify_chat_start(user1.telegram_id, user2.name, duration, user1.language or "en")
    await _notify_chat_start(user2.telegram_id, user1.name, duration, user2.language or "en")


async def cleanup_expired_sessions(db):
    """Called by scheduler every minute."""
    try:
        expired = db.execute(
            "SELECT id, user1_tg_id, user2_tg_id FROM chat_sessions WHERE is_active=1 AND expires_at IS NOT NULL AND expires_at < datetime('now')"
        ).fetchall()
        for session_id, tg1, tg2 in expired:
            try:
                db.execute("UPDATE chat_sessions SET is_active=0 WHERE id=?", (session_id,))
                db.commit()
                await _notify_chat_end(tg1)
                await _notify_chat_end(tg2)
            except Exception as e:
                print(f"[CHAT CLEANUP] session {session_id} failed: {e}")
    except Exception as e:
        print(f"[CHAT CLEANUP] error: {e}")


async def notify_missed_chats(db):
    """Called by scheduler — notify users who got a chat session but sent no message within 1 min."""
    try:
        # Sessions created more than 1 min ago, still active or just expired, with 0 messages
        cutoff = (datetime.utcnow() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        sessions = db.execute(
            """SELECT cs.id, cs.user1_tg_id, cs.user2_tg_id, cs.created_at
               FROM chat_sessions cs
               WHERE cs.created_at <= ?
               AND cs.is_premium_chat = 0
               AND NOT EXISTS (
                   SELECT 1 FROM chat_messages cm WHERE cm.session_id = cs.id
               )
               AND NOT EXISTS (
                   SELECT 1 FROM chat_messages cm2 WHERE cm2.session_id = cs.id AND cm2.sender_tg_id = 'missed_notified'
               )""",
            (cutoff,),
        ).fetchall()
        token = os.getenv("TELEGRAM_BOTS_KEY", "")
        if not token:
            return
        import httpx
        for session_id, tg1, tg2 in sessions:
            for tg_id in [tg1, tg2]:
                if not tg_id:
                    continue
                try:
                    async with httpx.AsyncClient(timeout=10) as client:
                        await client.post(
                            f"https://api.telegram.org/bot{token}/sendMessage",
                            json={
                                "chat_id": tg_id,
                                "text": "😔 *You missed a chat!*\n\nYour match was waiting but the chat window closed.\n\n👑 Upgrade to Premium for *unlimited chat time* so you never miss a connection!",
                                "parse_mode": "Markdown",
                            },
                        )
                except Exception as e:
                    print(f"[CHAT MISSED] notify failed for {tg_id}: {e}")
            # Mark as notified so we don't send again
            try:
                db.execute(
                    "INSERT INTO chat_messages (session_id, sender_tg_id, message) VALUES (?, 'missed_notified', '')",
                    (session_id,),
                )
                db.commit()
            except Exception:
                pass
    except Exception as e:
        print(f"[CHAT MISSED] error: {e}")


async def _notify_chat_start(tg_id: str, other_name: str, duration: str, lang: str):
    token = os.getenv("TELEGRAM_BOTS_KEY", "")
    if not tg_id or not token:
        return
    text = f"💬 *Chat started with {other_name}!*\n\n⏱ Duration: *{duration}*\n\nSend your messages here — they'll be forwarded directly."
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": tg_id, "text": text, "parse_mode": "Markdown"},
            )
    except Exception:
        pass


async def _notify_chat_end(tg_id: str):
    token = os.getenv("TELEGRAM_BOTS_KEY", "")
    if not tg_id or not token:
        return
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": tg_id,
                    "text": "⏰ *Chat session ended.*\n\nUpgrade to Premium for unlimited chat! 👑",
                    "parse_mode": "Markdown",
                },
            )
    except Exception:
        pass
