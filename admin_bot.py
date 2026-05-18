import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)

ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN", "")
ADMIN_TG_ID = os.getenv("ADMIN_TG_ID", "")
APP_URL = os.getenv("APP_URL", "")


def build_admin_bot() -> Application:
    app = Application.builder().token(ADMIN_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("pending", cmd_pending))
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
    from storage import photo_url
    import json
    photos = json.loads(user.photos or "[]")
    interests = json.loads(user.interests or "[]")
    text = (
        f"👤 <b>Pending Profile #{user.id}</b>\n\n"
        f"📛 Name: {user.name}\n"
        f"🎂 Age: {user.age}\n"
        f"⚡ Gender: {user.gender}\n"
        f"🏙 City: {user.city or '-'}\n"
        f"📝 Bio: {user.bio or '-'}\n"
        f"🏷 Interests: {', '.join(interests) or '-'}\n"
        f"📱 Social: {user.social_handle or '-'}\n"
        f"📞 Phone: {user.phone or '-'}\n"
        f"🌐 Lang: {user.language or 'en'}\n"
        f"📅 Joined: {(user.created_at or '')[:10]}"
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
        except Exception:
            pass
    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML",
                           reply_markup=keyboard)


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
    row = db.execute(
        f"SELECT {cols} FROM users WHERE is_approved=0 AND is_rejected=0 AND is_blocked=0 AND photo!='' ORDER BY created_at ASC LIMIT 1"
    ).fetchone()
    if not row:
        await update.message.reply_text("✅ No pending profiles!")
        return
    user = row_to_user(row)
    await _send_pending_profile(ctx.bot, update.effective_chat.id, user)


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    db = _get_db()
    total = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    approved = db.execute("SELECT COUNT(*) FROM users WHERE is_approved=1").fetchone()[0]
    pending = db.execute("SELECT COUNT(*) FROM users WHERE is_approved=0 AND is_rejected=0 AND is_blocked=0 AND photo!=''").fetchone()[0]
    premium = db.execute("SELECT COUNT(*) FROM users WHERE is_premium=1").fetchone()[0]
    matches = db.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    likes = db.execute("SELECT COUNT(*) FROM likes").fetchone()[0]
    text = (
        f"📊 <b>App Stats</b>\n\n"
        f"👥 Total users: {total}\n"
        f"✅ Approved: {approved}\n"
        f"⏳ Pending: {pending}\n"
        f"👑 Premium: {premium}\n"
        f"💕 Matches: {matches}\n"
        f"❤️ Total likes: {likes}"
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
    tg_ids = [r[0] for r in db.execute("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL AND is_blocked=0").fetchall()]
    sent, failed = 0, 0
    for tg_id in tg_ids:
        try:
            await ctx.bot.send_message(chat_id=tg_id, text=msg, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"✅ Sent: {sent} | ❌ Failed: {failed}")


async def cmd_remind(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    db = _get_db()
    rows = db.execute(
        "SELECT telegram_id, language FROM users WHERE photo='' AND is_blocked=0 AND telegram_id IS NOT NULL"
    ).fetchall()
    sent = 0
    for tg_id, lang in rows:
        lang = lang or "en"
        try:
            await ctx.bot.send_message(
                chat_id=tg_id,
                text="👋 Hey! You haven't completed your profile yet. Open the app to finish setup and start matching! 💕",
            )
            sent += 1
        except Exception:
            pass
    await update.message.reply_text(f"✅ Reminded {sent} incomplete users.")


async def cmd_remind_blocked(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    db = _get_db()
    rows = db.execute(
        "SELECT telegram_id, language FROM users WHERE is_rejected=1 AND telegram_id IS NOT NULL"
    ).fetchall()
    sent = 0
    for tg_id, lang in rows:
        try:
            await ctx.bot.send_message(
                chat_id=tg_id,
                text="ℹ️ Your profile was previously rejected. You can update your profile and resubmit for review.",
            )
            sent += 1
        except Exception:
            pass
    await update.message.reply_text(f"✅ Notified {sent} rejected users.")


async def cmd_find(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /find <name>")
        return
    name = " ".join(ctx.args)
    db = _get_db()
    rows = db.execute(
        "SELECT id, name, age, city, is_approved, is_blocked, telegram_id FROM users WHERE name LIKE ? LIMIT 5",
        (f"%{name}%",)
    ).fetchall()
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
    if not ctx.args:
        await update.message.reply_text("Usage: /deleteuser <id>")
        return
    try:
        user_id = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("Invalid ID.")
        return
    import libsql_experimental as libsql
    import os
    TURSO_URL = os.getenv("TURSO_DATABASE_URL", "")
    TURSO_TOKEN = os.getenv("TURSO_DATABASE_KEY", "")
    if TURSO_URL and TURSO_TOKEN:
        conn = libsql.connect(TURSO_URL, auth_token=TURSO_TOKEN)
    else:
        conn = libsql.connect("yourmeet.db")
    row = conn.execute("SELECT name FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        await update.message.reply_text("User not found.")
        return
    name = row[0]
    for tbl, c1, c2 in [
        ("likes", "from_user", "to_user"),
        ("matches", "user1_id", "user2_id"),
        ("skips", "user_id", "skipped_id"),
        ("referrals", "referrer_id", "referred_id"),
        ("reports", "reporter_id", "reported_id"),
        ("chat_sessions", "user1_id", "user2_id"),
    ]:
        conn.execute(f"DELETE FROM {tbl} WHERE {c1}=? OR {c2}=?", (user_id, user_id))
    conn.execute("DELETE FROM vibe_answers WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    await update.message.reply_text(f"✅ User #{user_id} ({name}) deleted permanently.")


async def cmd_confirm_cleanup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Actually delete all incomplete users."""
    if not _is_admin(update):
        return
    import libsql_experimental as libsql
    import os
    TURSO_URL = os.getenv("TURSO_DATABASE_URL", "")
    TURSO_TOKEN = os.getenv("TURSO_DATABASE_KEY", "")
    if TURSO_URL and TURSO_TOKEN:
        conn = libsql.connect(TURSO_URL, auth_token=TURSO_TOKEN)
    else:
        conn = libsql.connect("yourmeet.db")

    rows = conn.execute(
        "SELECT id FROM users WHERE (photo='' OR photo IS NULL) AND (age IS NULL OR age=0)"
    ).fetchall()
    if not rows:
        await update.message.reply_text("✅ Nothing to clean up!")
        return
    ids = [r[0] for r in rows]
    placeholders = ",".join("?" * len(ids))
    t = tuple(ids)
    t2 = tuple(ids + ids)
    conn.execute(f"DELETE FROM likes WHERE from_user IN ({placeholders}) OR to_user IN ({placeholders})", t2)
    conn.execute(f"DELETE FROM matches WHERE user1_id IN ({placeholders}) OR user2_id IN ({placeholders})", t2)
    conn.execute(f"DELETE FROM skips WHERE user_id IN ({placeholders}) OR skipped_id IN ({placeholders})", t2)
    conn.execute(f"DELETE FROM referrals WHERE referrer_id IN ({placeholders}) OR referred_id IN ({placeholders})", t2)
    conn.execute(f"DELETE FROM reports WHERE reporter_id IN ({placeholders}) OR reported_id IN ({placeholders})", t2)
    conn.execute(f"DELETE FROM chat_sessions WHERE user1_id IN ({placeholders}) OR user2_id IN ({placeholders})", t2)
    conn.execute(f"DELETE FROM vibe_answers WHERE user_id IN ({placeholders})", t)
    conn.execute(f"DELETE FROM users WHERE id IN ({placeholders})", t)
    conn.commit()
    await update.message.reply_text(f"✅ Deleted {len(ids)} incomplete users. Database is clean!")


# ── Callbacks ─────────────────────────────────────────────────────────────────

async def cb_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.split(":")[1])
    db = _get_db()
    db.execute("UPDATE users SET is_approved=1, is_rejected=0 WHERE id=?", (user_id,))
    db.commit()
    await _notify_user(ctx.bot, db, user_id, "profile_approved")
    await query.edit_message_caption(
        caption=f"✅ User #{user_id} approved.", reply_markup=None
    ) if query.message.photo else await query.edit_message_text(f"✅ User #{user_id} approved.")


async def cb_verify(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.split(":")[1])
    db = _get_db()
    db.execute("UPDATE users SET is_approved=1, is_verified=1, is_rejected=0 WHERE id=?", (user_id,))
    db.commit()
    await _notify_user(ctx.bot, db, user_id, "profile_approved_verified")
    await query.edit_message_caption(
        caption=f"⭐ User #{user_id} approved + verified.", reply_markup=None
    ) if query.message.photo else await query.edit_message_text(f"⭐ User #{user_id} approved + verified.")


async def cb_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.split(":")[1])
    db = _get_db()
    db.execute("UPDATE users SET is_rejected=1, is_approved=0 WHERE id=?", (user_id,))
    db.commit()
    await _notify_user(ctx.bot, db, user_id, "profile_rejected")
    await query.edit_message_caption(
        caption=f"❌ User #{user_id} rejected.", reply_markup=None
    ) if query.message.photo else await query.edit_message_text(f"❌ User #{user_id} rejected.")


async def cb_ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.split(":")[1])
    db = _get_db()
    db.execute("UPDATE users SET is_blocked=1, is_approved=0 WHERE id=?", (user_id,))
    db.commit()
    await _notify_user(ctx.bot, db, user_id, "profile_banned")
    await query.edit_message_caption(
        caption=f"🚫 User #{user_id} banned.", reply_markup=None
    ) if query.message.photo else await query.edit_message_text(f"🚫 User #{user_id} banned.")


async def cb_next_pending(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    db = _get_db()
    from database import row_to_user, USER_COLS
    cols = ", ".join(USER_COLS)
    row = db.execute(
        f"SELECT {cols} FROM users WHERE is_approved=0 AND is_rejected=0 AND is_blocked=0 AND photo!='' ORDER BY created_at ASC LIMIT 1"
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


async def send_for_review(user_id: int, name: str, age: int, gender: str, city: str, photo: str):
    """Called from setup router when new profile is submitted."""
    if not ADMIN_TG_ID or not ADMIN_BOT_TOKEN:
        return
    text = (
        f"🔔 <b>New Profile Submitted</b>\n\n"
        f"ID: {user_id} | {name}, {age} | {gender} | {city or '-'}\n\n"
        f"Use /pending to review."
    )
    try:
        from telegram import Bot
        bot = Bot(token=ADMIN_BOT_TOKEN)
        if photo:
            await bot.send_photo(
                chat_id=ADMIN_TG_ID, photo=photo,
                caption=text, parse_mode="HTML"
            )
        else:
            await bot.send_message(chat_id=ADMIN_TG_ID, text=text, parse_mode="HTML")
    except Exception as e:
        print(f"[ADMIN BOT] send_for_review failed: {e}")
