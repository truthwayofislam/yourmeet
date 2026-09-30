import os
import re
import json
import time
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)

ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN", "")
ADMIN_TG_ID = os.getenv("ADMIN_TG_ID", "").strip()
APP_URL = os.getenv("APP_URL", "")

from textsafe import esc

_rate_store: dict = {}
_rate_lock = threading.Lock()


def build_admin_bot() -> Application:
    if not ADMIN_BOT_TOKEN:
        raise RuntimeError("ADMIN_BOT_TOKEN is not set")
    app = Application.builder().token(ADMIN_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("pendingall", cmd_pending_all))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("remind", cmd_remind))
    app.add_handler(CommandHandler("remind_blocked", cmd_remind_blocked))
    app.add_handler(CommandHandler("find", cmd_find))
    app.add_handler(CommandHandler("user", cmd_user))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("cleanup", cmd_cleanup))
    app.add_handler(CommandHandler("deleteuser", cmd_delete_user))
    app.add_handler(CommandHandler("confirmcleanup", cmd_confirm_cleanup))
    app.add_handler(CommandHandler("fixuser", cmd_fix_user))
    app.add_handler(CommandHandler("auditlog", cmd_audit_log))
    app.add_handler(CallbackQueryHandler(cb_approve, pattern=r"^approve:"))
    app.add_handler(CallbackQueryHandler(cb_verify, pattern=r"^verify:"))
    app.add_handler(CallbackQueryHandler(cb_reject, pattern=r"^reject:"))
    app.add_handler(CallbackQueryHandler(cb_ban, pattern=r"^ban:"))
    app.add_handler(CallbackQueryHandler(cb_next_pending, pattern=r"^next_pending$"))
    app.add_error_handler(admin_error_handler)
    return app


async def admin_error_handler(update, context):
    from telegram.error import TimedOut, NetworkError, RetryAfter
    err = context.error
    if isinstance(err, (TimedOut, NetworkError)):
        print(f"[ADMIN BOT] Transient error ignored: {err}")
        return
    if isinstance(err, RetryAfter):
        print(f"[ADMIN BOT] Rate limited, retry after {err.retry_after}s")
        return
    import traceback
    print(f"[ADMIN BOT] Unhandled error: {traceback.format_exc()}")


def _is_admin(update: Update) -> bool:
    return str(update.effective_user.id) == ADMIN_TG_ID


def _get_db():
    from database import get_conn
    return get_conn()


# Telegram file_ids are opaque tokens with no fixed length, but they always
# consist of a single segment of base64url characters. Reject anything that
# isn't a plausible file_id (URLs, path traversal, injection payloads).
_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,200}$")


def _valid_file_id(file_id: str) -> bool:
    return bool(file_id) and bool(_FILE_ID_RE.match(file_id))


def _admin_rate_limit(update: Update, limit: int = 10, window: int = 60) -> bool:
    """Throttle admin commands. Returns True if allowed, False if rate-limited."""
    uid = str(update.effective_user.id)
    key = f"admin_cmd:{uid}"
    now = time.time()
    with _rate_lock:
        stamps = [t for t in _rate_store.get(key, []) if now - t < window]
        if len(stamps) >= limit:
            _rate_store[key] = stamps
            return False
        stamps.append(now)
        _rate_store[key] = stamps
        return True


def _approval_keyboard(user_id: int):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve:{user_id}"),
            InlineKeyboardButton("⭐ Approve+Verify", callback_data=f"verify:{user_id}"),
        ],
        [
            InlineKeyboardButton("❌ Reject", callback_data=f"reject:{user_id}"),
            InlineKeyboardButton("🚫 Ban", callback_data=f"ban:{user_id}"),
        ],
        [InlineKeyboardButton("⏭ Next Pending", callback_data="next_pending")],
    ])


async def _send_pending_profile(bot, chat_id: str, user):
    interests = []
    try:
        interests = json.loads(user.interests or "[]") or []
    except (ValueError, TypeError):
        interests = []
    text = (
        f"\U0001f464 <b>Pending Profile #{user.id}</b>\n\n"
        f"Nickname: {esc(user.name)}\n"
        f"Age: {user.age}\n"
        f"Gender: {esc(user.gender)}\n"
        f"City: {esc(user.city) or '-'}\n"
        f"Bio: {esc(user.bio) or '-'}\n"
        f"Interests: {', '.join(esc(i) for i in interests) or '-'}\n"
        f"Social: {esc(user.social_handle) or '-'}\n"
        f"Language: {user.language or 'en'}\n"
        f"Joined: {(user.created_at or '')[:10]}"
    )
    keyboard = _approval_keyboard(user.id)
    if user.photo:
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=user.photo,
                caption=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return
        except Exception as e:
            print(f"[ADMIN BOT] send_photo failed: {e}")
    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=keyboard)


# ── Commands ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    await update.message.reply_text("👋 <b>YourMeet Admin Bot</b>\n\nUse /pending to review profiles.", parse_mode="HTML")


async def cmd_pending(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    db = _get_db()
    from database import row_to_user, USER_COLS
    cols = ", ".join(USER_COLS)
    try:
        row = db.execute(
            f"SELECT {cols} FROM users WHERE is_approved=0 AND is_rejected=0 AND is_blocked=0"
            f" AND photo!='' AND age IS NOT NULL AND age != 0 AND gender IS NOT NULL AND gender != ''"
            f" ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
    except Exception as e:
        await update.message.reply_text(f"❌ DB error: {e}")
        return
    if not row:
        await update.message.reply_text("✅ No pending profiles!")
        return
    user = row_to_user(row)
    await _send_pending_profile(ctx.bot, update.effective_chat.id, user)


async def cmd_pending_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show ALL users with their exact status — to debug missing profiles."""
    if not _is_admin(update):
        return
    db = _get_db()
    rows = db.execute(
        "SELECT id, name, age, gender, photo, is_approved, is_rejected, is_blocked FROM users ORDER BY id DESC LIMIT 30"
    ).fetchall()
    if not rows:
        await update.message.reply_text("No users found.")
        return
    lines = []
    for r in rows:
        uid, name, age, gender, photo, approved, rejected, blocked = r
        g = "👩" if gender == "female" else ("👨" if gender == "male" else "❓")
        p = "📷" if photo else "❌"
        if blocked:
            st = "🚫BAN"
        elif rejected:
            st = "❌REJ"
        elif approved:
            st = "✅APR"
        else:
            st = "⏳PND" if (photo and age and gender) else "👻INC"
        lines.append(f"#{uid} {g}{p} {st} {name or '?'}, {age or '?'}")
    header = "📋 <b>All Users (latest 30):</b>\n\n"
    chunks = [lines[i:i+20] for i in range(0, len(lines), 20)]
    for chunk in chunks:
        await update.message.reply_text(header + "\n".join(chunk), parse_mode="HTML")
        header = ""


async def cmd_fix_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Reset a user to pending — /fixuser <id>"""
    if not _is_admin(update):
        return
    if not _admin_rate_limit(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /fixuser <id>")
        return
    try:
        user_id = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("Invalid ID.")
        return
    db = _get_db()
    row = db.execute("SELECT id, name, is_approved, is_rejected, is_blocked FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        await update.message.reply_text("User not found.")
        return
    db.execute("UPDATE users SET is_approved=0, is_rejected=0, is_blocked=0 WHERE id=?", (user_id,))
    db.commit()
    from database import log_audit
    log_audit(str(update.effective_user.id), "fix_user", user_id)
    await update.message.reply_text(
        f"✅ User #{user_id} ({row[1]}) reset to pending.\n"
        f"Was: approved={row[2]} rejected={row[3]} blocked={row[4]}"
    )


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    db = _get_db()
    total = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    approved = db.execute("SELECT COUNT(*) FROM users WHERE is_approved=1").fetchone()[0]
    pending = db.execute("SELECT COUNT(*) FROM users WHERE is_approved=0 AND is_rejected=0 AND is_blocked=0 AND (photo IS NOT NULL AND photo!='') AND age IS NOT NULL AND gender IS NOT NULL").fetchone()[0]
    premium = db.execute("SELECT COUNT(*) FROM users WHERE is_premium=1").fetchone()[0]
    matches = db.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    likes = db.execute("SELECT COUNT(*) FROM likes").fetchone()[0]
    active_chats = db.execute("SELECT COUNT(*) FROM chat_sessions WHERE is_active=1").fetchone()[0]
    db.close()
    text = (
        f"📊 <b>App Stats</b>\n\n"
        f"👥 Total users: {total}\n"
        f"✅ Approved: {approved}\n"
        f"⏳ Pending: {pending}\n"
        f"👑 Premium: {premium}\n"
        f"💕 Matches: {matches}\n"
        f"❤️ Total likes: {likes}\n"
        f"💬 Active chats: {active_chats}"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    msg = " ".join(ctx.args)
    db = _get_db()
    tg_ids = [r[0] for r in db.execute("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL AND is_blocked=0 AND is_approved=1").fetchall()]
    db.close()
    if not tg_ids:
        await update.message.reply_text("No approved users to broadcast to.")
        return
    sent, failed = 0, 0
    import asyncio
    for tg_id in tg_ids:
        try:
            if await _send_remind(tg_id, msg, parse_mode=None):
                sent += 1
            else:
                failed += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await update.message.reply_text(f"✅ Sent: {sent} | ❌ Failed: {failed}")


async def cmd_remind(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    if not _admin_rate_limit(update, limit=5, window=60):
        await update.message.reply_text("⏳ Too many commands. Please wait a moment.")
        return
    db = _get_db()
    rows = db.execute(
        "SELECT telegram_id, language FROM users WHERE (photo IS NULL OR photo='') AND is_blocked=0 AND is_rejected=0 AND is_approved=0 AND telegram_id IS NOT NULL"
    ).fetchall()
    db.close()
    if not rows:
        await update.message.reply_text("✅ No incomplete users to remind.")
        return
    sent = 0
    import asyncio
    for tg_id, lang in rows:
        lang = lang or "en"
        try:
            if await _send_remind(tg_id, "👋 Hey! You haven't completed your profile yet. Send /start to finish setup and start matching! 💕"):
                sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    from database import log_audit
    log_audit(str(update.effective_user.id), "remind", detail=f"sent={sent}")
    await update.message.reply_text(f"✅ Reminded {sent} incomplete users.")


async def cmd_remind_blocked(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    if not _admin_rate_limit(update, limit=5, window=60):
        await update.message.reply_text("⏳ Too many commands. Please wait a moment.")
        return
    db = _get_db()
    rows = db.execute(
        "SELECT telegram_id, language FROM users WHERE is_rejected=1 AND is_blocked=0 AND telegram_id IS NOT NULL"
    ).fetchall()
    db.close()
    if not rows:
        await update.message.reply_text("✅ No rejected users to notify.")
        return
    sent = 0
    import asyncio
    for tg_id, lang in rows:
        try:
            if await _send_remind(tg_id, "ℹ️ Your profile was previously rejected. Send /start to update your profile and resubmit for review."):
                sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    from database import log_audit
    log_audit(str(update.effective_user.id), "remind_blocked", detail=f"sent={sent}")
    await update.message.reply_text(f"✅ Notified {sent} rejected users.")


async def cmd_find(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /find <name or telegram_id>")
        return
    query_str = " ".join(ctx.args)
    db = _get_db()
    # Try exact telegram_id match first
    rows = db.execute(
        "SELECT id, name, age, city, is_approved, is_blocked, telegram_id FROM users WHERE telegram_id=? LIMIT 1",
        (query_str,)
    ).fetchall()
    if not rows:
        rows = db.execute(
            "SELECT id, name, age, city, is_approved, is_blocked, telegram_id FROM users WHERE name LIKE ? LIMIT 5",
            (f"%{query_str}%",)
        ).fetchall()
    db.close()
    if not rows:
        await update.message.reply_text("No users found.")
        return
    text = "\n\n".join(
        f"ID: {r[0]} | {r[1]}, {r[2]} | {r[3] or '-'} | {'✅' if r[4] else '⏳'} | {'🚫' if r[5] else '✔️'} | tg:{r[6]}"
        for r in rows
    )
    await update.message.reply_text(f"🔍 Results:\n\n{text}")


async def cmd_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /user <id>")
        return
    try:
        user_id = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("Invalid ID.")
        return
    db = _get_db()
    from database import row_to_user, USER_COLS
    cols = ", ".join(USER_COLS)
    row = db.execute(f"SELECT {cols} FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        await update.message.reply_text("User not found.")
        return
    user = row_to_user(row)
    await _send_pending_profile(ctx.bot, update.effective_chat.id, user)


async def cmd_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """List all users with basic info."""
    if not _is_admin(update):
        return
    db = _get_db()
    rows = db.execute(
        "SELECT id, name, age, telegram_id, photo, is_approved, is_premium, created_at FROM users ORDER BY id"
    ).fetchall()
    if not rows:
        await update.message.reply_text("No users found.")
        return
    lines = []
    for r in rows:
        uid, name, age, tg_id, photo, approved, premium, created = r
        status = "✅" if approved else "⏳"
        has_photo = "📷" if photo else "❌"
        prem = "👑" if premium else ""
        lines.append(f"#{uid} {has_photo}{prem} {status} {name or '?'}, {age or '?'} | tg:{tg_id or '-'} | {(created or '')[:10]}")
    text = "👥 <b>All Users:</b>\n\n" + "\n".join(lines)
    # Split if too long
    if len(text) > 4000:
        chunks = [lines[i:i+20] for i in range(0, len(lines), 20)]
        for chunk in chunks:
            await update.message.reply_text("👥 <b>Users:</b>\n\n" + "\n".join(chunk), parse_mode="HTML")
    else:
        await update.message.reply_text(text, parse_mode="HTML")


async def cmd_cleanup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Delete incomplete users — no photo, no age, no telegram_id."""
    if not _is_admin(update):
        return
    if not _admin_rate_limit(update):
        return
    db = _get_db()
    # Find ghost users: no photo AND no age (never completed setup)
    rows = db.execute(
        "SELECT id, name, telegram_id, created_at FROM users WHERE (photo='' OR photo IS NULL) AND (age IS NULL OR age=0)"
    ).fetchall()
    if not rows:
        await update.message.reply_text("✅ No incomplete users to clean up!")
        return
    text = f"🗑 Found <b>{len(rows)}</b> incomplete users:\n\n"
    for r in rows:
        text += f"#{r[0]} {r[1] or '?'} | tg:{r[2] or '-'} | {(r[3] or '')[:10]}\n"
    text += "\nReply /confirmcleanup to delete them all."
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_delete_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Delete a specific user by ID — /deleteuser <id>"""
    if not _is_admin(update):
        return
    if not _admin_rate_limit(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /deleteuser <id>")
        return
    try:
        user_id = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("Invalid ID.")
        return
    db = _get_db()
    row = db.execute("SELECT name FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        await update.message.reply_text("User not found.")
        return
    name = row[0]
    from routers.auth import _delete_user_data
    _delete_user_data(db, user_id)
    from database import log_audit
    log_audit(str(update.effective_user.id), "delete_user", user_id)
    await update.message.reply_text(f"✅ User #{user_id} ({name}) deleted permanently.")


async def cmd_confirm_cleanup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Actually delete all incomplete users."""
    if not _is_admin(update):
        return
    if not _admin_rate_limit(update):
        return
    db = _get_db()
    rows = db.execute(
        "SELECT id FROM users WHERE (photo='' OR photo IS NULL) AND (age IS NULL OR age=0)"
    ).fetchall()
    if not rows:
        await update.message.reply_text("✅ Nothing to clean up!")
        return
    from routers.auth import _delete_user_data
    from database import log_audit
    for (uid,) in rows:
        _delete_user_data(db, uid)
        log_audit(str(update.effective_user.id), "cleanup_delete_user", uid)
    await update.message.reply_text(f"✅ Deleted {len(rows)} incomplete users. Database is clean!")


async def cmd_audit_log(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show recent admin actions — /auditlog [limit]"""
    if not _is_admin(update):
        return
    if not _admin_rate_limit(update):
        return
    limit = 20
    if ctx.args:
        try:
            limit = max(1, min(100, int(ctx.args[0])))
        except ValueError:
            pass
    db = _get_db()
    rows = db.execute(
        "SELECT tg_id, action, target_id, detail, created_at FROM audit_log ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    if not rows:
        await update.message.reply_text("📝 No audit log entries yet.")
        return
    lines = []
    for tg_id, action, target_id, detail, created_at in rows:
        lines.append(f"• {created_at[:16]} | {tg_id} | {action} | id={target_id} | {detail or ''}")
    text = "📝 <b>Audit Log</b>\n\n" + "\n".join(lines)
    if len(text) > 4000:
        text = text[:3900] + "\n...truncated"
    await update.message.reply_text(text, parse_mode="HTML")


# ── Callbacks ─────────────────────────────────────────────────────────────────

async def cb_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.split(":")[1])
    db = _get_db()
    db.execute("UPDATE users SET is_approved=1, is_rejected=0 WHERE id=?", (user_id,))
    db.commit()
    from database import log_audit
    log_audit(str(update.effective_user.id), "approve_user", user_id)
    await _notify_user(ctx.bot, db, user_id, "profile_approved")
    await query.edit_message_caption(
        caption=f"✅ User #{user_id} approved.", reply_markup=None
    ) if query.message.photo else await query.edit_message_text(f"✅ User #{user_id} approved.")


async def cb_verify(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.split(":")[1])
    db = _get_db()
    db.execute("UPDATE users SET is_approved=1, is_verified=1, is_rejected=0 WHERE id=?", (user_id,))
    db.commit()
    from database import log_audit
    log_audit(str(update.effective_user.id), "verify_user", user_id)
    await _notify_user(ctx.bot, db, user_id, "profile_approved_verified")
    await query.edit_message_caption(
        caption=f"⭐ User #{user_id} approved + verified.", reply_markup=None
    ) if query.message.photo else await query.edit_message_text(f"⭐ User #{user_id} approved + verified.")


async def cb_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.split(":")[1])
    db = _get_db()
    db.execute("UPDATE users SET is_rejected=1, is_approved=0 WHERE id=?", (user_id,))
    db.commit()
    from database import log_audit
    log_audit(str(update.effective_user.id), "reject_user", user_id)
    await _notify_user(ctx.bot, db, user_id, "profile_rejected")
    await query.edit_message_caption(
        caption=f"❌ User #{user_id} rejected.", reply_markup=None
    ) if query.message.photo else await query.edit_message_text(f"❌ User #{user_id} rejected.")


async def cb_ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.split(":")[1])
    db = _get_db()
    db.execute("UPDATE users SET is_blocked=1, is_approved=0 WHERE id=?", (user_id,))
    db.commit()
    from database import log_audit
    log_audit(str(update.effective_user.id), "ban_user", user_id)
    await _notify_user(ctx.bot, db, user_id, "profile_banned")
    await query.edit_message_caption(
        caption=f"🚫 User #{user_id} banned.", reply_markup=None
    ) if query.message.photo else await query.edit_message_text(f"🚫 User #{user_id} banned.")


async def cb_next_pending(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    query = update.callback_query
    await query.answer()
    db = _get_db()
    from database import row_to_user, USER_COLS
    cols = ", ".join(USER_COLS)
    row = db.execute(
        f"SELECT {cols} FROM users WHERE is_approved=0 AND is_rejected=0 AND is_blocked=0"
        f" AND photo!='' AND age IS NOT NULL AND age != 0 AND gender IS NOT NULL AND gender != ''"
        f" ORDER BY created_at ASC LIMIT 1"
    ).fetchone()
    if not row:
        await ctx.bot.send_message(chat_id=query.message.chat_id, text="✅ No more pending profiles!")
        return
    user = row_to_user(row)
    await _send_pending_profile(ctx.bot, query.message.chat_id, user)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _notify_user(bot, db, user_id: int, string_key: str):
    from strings import get as s
    from telegram import Bot
    main_bot_token = os.getenv("TELEGRAM_BOTS_KEY", "").strip().strip("'\"")
    row = db.execute("SELECT telegram_id, language FROM users WHERE id=?", (user_id,)).fetchone()
    if not row or not row[0]:
        return
    tg_id, lang = row
    lang = lang or "en"
    try:
        notify_bot = Bot(token=main_bot_token) if main_bot_token else bot
        await notify_bot.send_message(chat_id=tg_id, text=s(lang, string_key), parse_mode="HTML")
    except Exception as e:
        print(f"[ADMIN BOT] notify failed: {e}")


async def _send_remind(tg_id: str, text: str, parse_mode: str = "HTML") -> bool:
    """Send a broadcast reminder via the MAIN user bot.

    Users have chatted with the main bot during /start setup, so Telegram
    allows it to message them. The admin bot has never talked to them, so
    messages sent from ctx.bot are silently blocked by Telegram.
    """
    from telegram import Bot
    main_bot_token = os.getenv("TELEGRAM_BOTS_KEY", "").strip().strip("'\"")
    if not main_bot_token:
        return False
    try:
        await Bot(token=main_bot_token).send_message(chat_id=tg_id, text=text, parse_mode=parse_mode)
        return True
    except Exception as e:
        print(f"[ADMIN BOT] remind send failed to {tg_id}: {e}")
        return False


async def send_for_review(user_id: int, name: str, age: int, gender: str, city: str, photo: str):
    """Called from setup router when new profile is submitted."""
    if not ADMIN_TG_ID or not ADMIN_BOT_TOKEN:
        return
    if photo and not _valid_file_id(photo):
        photo = ""
    text = (
        f"\U0001f514 <b>New Profile Submitted</b>\n\n"
        f"ID: {user_id} | {esc(name)}, {age} | {esc(gender)} | {esc(city) or '-'}"
    )
    keyboard = _approval_keyboard(user_id)
    try:
        from telegram import Bot
        bot = Bot(token=ADMIN_BOT_TOKEN)
        if photo:
            try:
                await bot.send_photo(
                    chat_id=ADMIN_TG_ID, photo=photo,
                    caption=text, parse_mode="HTML",
                    reply_markup=keyboard,
                )
                return
            except Exception as e:
                print(f"[ADMIN BOT] send_for_review photo failed: {e}")
        await bot.send_message(
            chat_id=ADMIN_TG_ID, text=text, parse_mode="HTML",
            reply_markup=keyboard
        )
    except Exception as e:
        print(f"[ADMIN BOT] send_for_review failed: {e}")
