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
    app.add_handler(CallbackQueryHandler(cb_approve, pattern=r"^approve:"))
    app.add_handler(CallbackQueryHandler(cb_verify, pattern=r"^verify:"))
    app.add_handler(CallbackQueryHandler(cb_reject, pattern=r"^reject:"))
    app.add_handler(CallbackQueryHandler(cb_ban, pattern=r"^ban:"))
    app.add_handler(CallbackQueryHandler(cb_next_pending, pattern=r"^next_pending$"))
    return app


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
