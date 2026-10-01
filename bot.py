import os
import json
import warnings
warnings.filterwarnings("ignore", message=".*per_message=False.*", category=UserWarning)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes,
)
from strings import get as s
from textsafe import esc
import ratelimit

BOT_TOKEN = os.getenv("TELEGRAM_BOTS_KEY", "")
APP_URL = os.getenv("APP_URL", "")

# Conversation states
(
    SETUP_NAME, SETUP_AGE, SETUP_GENDER, SETUP_INTERESTED_IN,
    SETUP_CITY, SETUP_BIO, SETUP_SOCIAL, SETUP_LOOKING_FOR, SETUP_PHOTO,
) = range(9)


def build_bot() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    setup_conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CallbackQueryHandler(cb_setup_start, pattern=r"^setup:start$"),
            # MUST be a conversation entry point: cb_terms_accept returns
            # SETUP_NAME, which only starts the conversation when the handler
            # belongs to it. As a standalone handler the return is ignored and
            # the user's first message falls through to handle_message,
            # leaving every new signup stuck at the name step.
            CallbackQueryHandler(cb_terms_accept, pattern=r"^terms:accept$"),
            CommandHandler("editprofile", cmd_edit_profile),
        ],
        states={
            SETUP_NAME:          [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_name)],
            SETUP_AGE:           [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_age)],
            SETUP_GENDER:        [CallbackQueryHandler(setup_gender, pattern=r"^sg:")],
            SETUP_INTERESTED_IN: [CallbackQueryHandler(setup_interested_in, pattern=r"^si:")],
            SETUP_CITY:          [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_city)],
            SETUP_BIO:           [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_bio)],
            SETUP_SOCIAL:        [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_social)],
            SETUP_LOOKING_FOR:   [CallbackQueryHandler(setup_looking_for, pattern=r"^lf:")],
            SETUP_PHOTO:         [
                MessageHandler(filters.PHOTO, setup_photo),
                # Text at the photo step must NOT fall through to chat routing
                # — guide the user back instead of a dead end.
                MessageHandler(filters.TEXT & ~filters.COMMAND, setup_photo_remind),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
        per_chat=True,
        per_user=True,
        per_message=False,
    )

    app.add_handler(setup_conv)
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("browse", cmd_browse))
    app.add_handler(CommandHandler("matches", cmd_matches))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("premium", cmd_premium))
    app.add_handler(CommandHandler("share", cmd_share))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("confirmdelete", cmd_confirm_delete))
    app.add_handler(CommandHandler("language", cmd_language))
    app.add_handler(CommandHandler("boost", cmd_boost))
    app.add_handler(CommandHandler("mystery", cmd_mystery))
    app.add_handler(CommandHandler("likes", cmd_likes))
    app.add_handler(CommandHandler("block", cmd_block))
    app.add_handler(CommandHandler("filters", cmd_filters))
    app.add_handler(CallbackQueryHandler(cb_language, pattern=r"^lang:"))
    app.add_handler(CallbackQueryHandler(cb_buy, pattern=r"^buy:"))
    app.add_handler(CallbackQueryHandler(cb_filter, pattern=r"^filter:"))
    app.add_handler(CallbackQueryHandler(cb_vibe, pattern=r"^vibe:"))
    app.add_handler(CallbackQueryHandler(cb_report, pattern=r"^report:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_report_reason, pattern=r"^reportreason:"))
    app.add_handler(CallbackQueryHandler(cb_like, pattern=r"^like:"))
    app.add_handler(CallbackQueryHandler(cb_skip, pattern=r"^skip:"))
    app.add_handler(CallbackQueryHandler(cb_superlike, pattern=r"^superlike:"))
    app.add_handler(CallbackQueryHandler(cb_next, pattern=r"^next$"))
    app.add_handler(CallbackQueryHandler(cb_unmatch, pattern=r"^unmatch:"))
    app.add_handler(CallbackQueryHandler(cb_block_user, pattern=r"^blockuser:"))
    app.add_handler(CallbackQueryHandler(cb_chat_match, pattern=r"^chat_match:"))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(CallbackQueryHandler(cb_cmd, pattern=r"^cmd:"))
    from telegram.ext import PreCheckoutQueryHandler
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)
    return app


async def error_handler(update, context):
    import traceback
    from telegram.error import TimedOut, NetworkError, RetryAfter
    err = context.error
    if isinstance(err, (TimedOut, NetworkError)):
        return
    if isinstance(err, RetryAfter):
        return
    print(f"[BOT] Error: {traceback.format_exc()}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _lang(update: Update) -> str:
    return (update.effective_user.language_code or "en")[:2]


def _get_user(tg_id: str):
    from database import get_conn, row_to_user, USER_COLS
    db = get_conn()
    cols = ", ".join(USER_COLS)
    row = db.execute(f"SELECT {cols} FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
    db.close()
    return row_to_user(row)


async def _cleanup_chat(ctx, chat_id: int):
    last_id = ctx.user_data.get("last_keyboard_msg_id")
    if last_id:
        try:
            await ctx.bot.delete_message(chat_id=chat_id, message_id=last_id)
        except Exception:
            pass
        ctx.user_data["last_keyboard_msg_id"] = None

    recent = ctx.user_data.get("recent_bot_msg_ids", [])
    if not recent:
        return
    for mid in recent:
        try:
            await ctx.bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass
    ctx.user_data["recent_bot_msg_ids"] = []


async def _track_bot_message(ctx, message_id: int):
    recent = ctx.user_data.get("recent_bot_msg_ids", [])
    recent.append(message_id)
    if len(recent) > 20:
        recent = recent[-20:]
    ctx.user_data["recent_bot_msg_ids"] = recent


async def _reply(ctx, chat_id, text, **kwargs):
    """Send a message with the persistent reply keyboard attached.

    PTB 21.3 has no set_default_reply_keyboard, so every response must
    carry the keyboard explicitly. This helper makes that uniform.
    """
    kwargs.setdefault("parse_mode", "HTML")
    kwargs.setdefault("reply_markup", _main_keyboard())
    sent = await ctx.bot.send_message(chat_id=chat_id, text=text, **kwargs)
    await _track_bot_message(ctx, sent.message_id)
    return sent


def _main_keyboard():
    """Persistent reply keyboard — stays visible below the text input."""
    return ReplyKeyboardMarkup(
        [
            ["🔍 Browse", "💕 Matches", "👤 Profile", "📊 Stats"],
            ["👑 Premium", "📖 Commands", "🌐 Language", "🚀 Boost"],
            ["🎯 Filters", "✏️ Edit", "🚫 Block", "🗑 Delete"],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Type a command or tap a button 👇",
    )


def _browse_keyboard(target_id: int, super_left: int):
    if super_left >= 999999:
        sl_text = "⭐ Super Like (∞)"
    elif super_left > 0:
        sl_text = f"⭐ Super Like ({super_left})"
    else:
        sl_text = "⭐ Super Like (0)"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("❤️ Like", callback_data=f"like:{target_id}"),
            InlineKeyboardButton("👎 Skip", callback_data=f"skip:{target_id}"),
        ],
        [InlineKeyboardButton(sl_text, callback_data=f"superlike:{target_id}")],
        [InlineKeyboardButton("⚠️ Report", callback_data=f"report:{target_id}")],
    ])


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    tg_id = str(tg_user.id)
    lang = (tg_user.language_code or "en")[:2]

    if ctx.args:
        ref_code = ctx.args[0]
        if ref_code.startswith("ref_"):
            ctx.user_data["pending_ref"] = ref_code[4:]

    user = _get_user(tg_id)
    if user:
        lang = user.language or lang

    # Terms not accepted
    if not user or not getattr(user, "terms_accepted", 0):
        text = (
            "👋 Welcome to <b>YourMeet</b>! 💕\n\n"
            "<b>Before you start:</b>\n"
            f"• <a href='{APP_URL}/terms'>Terms of Service</a>\n"
            f"• <a href='{APP_URL}/privacy'>Privacy Policy</a>\n\n"
            "✅ Tap <b>I Agree</b> to confirm you are <b>18+</b>"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ I Agree — Let's Go!", callback_data="terms:accept"),
        ]])
        await _cleanup_chat(ctx, update.effective_chat.id)
        sent = await update.message.reply_text(text, parse_mode="HTML",
                                        reply_markup=keyboard,
                                        disable_web_page_preview=True)
        ctx.user_data["last_keyboard_msg_id"] = sent.message_id
        await _track_bot_message(ctx, sent.message_id)
        return ConversationHandler.END

    # Profile incomplete — start setup
    if not user or not user.photo or not user.age or not user.gender:
        m = await update.message.reply_text(
            "👋 Welcome back! Let's complete your profile.\n\nWhat's your <b>name</b>?",
            parse_mode="HTML"
        )
        _save_setup_data(tg_id, msg_id=m.message_id)
        await _track_bot_message(ctx, m.message_id)
        return SETUP_NAME

    # Fully set up
    await _cleanup_chat(ctx, update.effective_chat.id)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔍 Browse Profiles", callback_data="next"),
            InlineKeyboardButton("💕 Matches", callback_data="cmd:matches"),
        ],
        [
            InlineKeyboardButton("👤 My Profile", callback_data="cmd:profile"),
            InlineKeyboardButton("📊 Stats", callback_data="cmd:stats"),
        ],
        [
            InlineKeyboardButton("👑 Premium", callback_data="cmd:premium"),
            InlineKeyboardButton("📖 Commands", callback_data="cmd:help"),
        ],
    ])
    sent = await update.message.reply_text(
        s(lang, "welcome"),
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    ctx.user_data["last_keyboard_msg_id"] = sent.message_id
    await _track_bot_message(ctx, sent.message_id)
    return ConversationHandler.END


async def cb_terms_accept(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_user = update.effective_user
    tg_id = str(tg_user.id)
    lang = (tg_user.language_code or "en")[:2]

    from database import get_conn, row_to_user, USER_COLS
    db = get_conn()
    cols = ", ".join(USER_COLS)
    row = db.execute(f"SELECT {cols} FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
    if not row:
        db.execute(
            "INSERT INTO users (name, telegram_id, language, terms_accepted) VALUES (?,?,?,1)",
            (tg_user.first_name or "User", tg_id, lang),
        )
    else:
        db.execute("UPDATE users SET terms_accepted=1 WHERE telegram_id=?", (tg_id,))
    db.commit()
    db.close()

    # Edit same message — set last_bot_msg so setup chain continues on this message
    await query.edit_message_text(
        "✅ <b>Terms accepted!</b>\n\nLet's set up your profile! 🎉\n\nWhat's your <b>name</b>?",
        parse_mode="HTML"
    )
    _save_setup_data(tg_id, msg_id=query.message.message_id)
    return SETUP_NAME


async def cb_setup_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(update.effective_user.id)
    await query.edit_message_text(
        "Let's set up your profile! 🎉\n\nWhat's your <b>name</b>?",
        parse_mode="HTML"
    )
    _save_setup_data(tg_id, msg_id=query.message.message_id)
    return SETUP_NAME


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    sent = await update.message.reply_text("Setup cancelled. Use /start to begin again.")
    await _track_bot_message(ctx, sent.message_id)
    return ConversationHandler.END


async def cmd_about(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _cleanup_chat(ctx, update.effective_chat.id)
    await _reply(ctx, update.effective_chat.id,
        "ℹ️ <b>About YourMeet</b>\n\n"
        "YourMeet is a dating app where you swipe, match, and chat — all through Telegram.\n\n"
        "Features:\n"
        "• Swipe & match with people nearby\n"
        "• 1-on-1 chat via bot forwarding\n"
        "• Vibe Check questions on every match\n"
        "• Mystery Mode (Premium) — hide your photo\n"
        "• Boost your profile for more visibility (Premium)\n"
        "• See who liked you (Premium)\n\n"
        "Use /premium to unlock Premium features. 👑"
    )


# ── Setup Conversation ────────────────────────────────────────────────────────

async def _get_setup_data(tg_id: str) -> dict:
    try:
        from database import get_conn
        import json as _j
        db = get_conn()
        row = db.execute(
            "SELECT setup_msg_id, setup_data FROM users WHERE telegram_id=?", (tg_id,)
        ).fetchone()
        db.close()
        if row:
            return {"msg_id": row[0] or 0, "data": _j.loads(row[1] or "{}")}
    except Exception:
        pass
    return {"msg_id": 0, "data": {}}


def _save_setup_data(tg_id: str, msg_id=None, data=None):
    try:
        from database import get_conn
        import json as _j
        db = get_conn()
        if msg_id is not None and data is not None:
            db.execute(
                "UPDATE users SET setup_msg_id=?, setup_data=? WHERE telegram_id=?",
                (msg_id, _j.dumps(data), tg_id)
            )
        elif msg_id is not None:
            db.execute("UPDATE users SET setup_msg_id=? WHERE telegram_id=?", (msg_id, tg_id))
        elif data is not None:
            db.execute(
                "UPDATE users SET setup_data=? WHERE telegram_id=?",
                (_j.dumps(data), tg_id)
            )
        db.commit()
        db.close()
    except Exception as e:
        print(f"[SETUP] save failed: {e}")


async def _edit_setup_msg(ctx, chat_id, tg_id, text, parse_mode=None, reply_markup=None):
    s = await _get_setup_data(tg_id)
    msg_id = s["msg_id"]
    if msg_id:
        try:
            await ctx.bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text=text, parse_mode=parse_mode, reply_markup=reply_markup
            )
            return
        except Exception:
            pass
    m = await ctx.bot.send_message(
        chat_id=chat_id, text=text,
        parse_mode=parse_mode, reply_markup=reply_markup
    )
    _save_setup_data(tg_id, msg_id=m.message_id)
    await _track_bot_message(ctx, m.message_id)


# Reply-keyboard labels a user may tap mid-setup — these must never be
# accepted as profile field values.
_UI_BUTTONS = {
    "🔍 Browse", "💕 Matches", "👤 Profile", "📊 Stats", "👑 Premium",
    "📖 Commands", "🌐 Language", "🚀 Boost", "🎯 Filters", "✏️ Edit",
    "🚫 Block", "🗑 Delete",
}


async def setup_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id
    name = update.message.text.strip()[:50]
    try:
        await update.message.delete()
    except Exception:
        pass
    if len(name) < 2:
        await _edit_setup_msg(ctx, chat_id, tg_id, "Name too short. Please enter your real name:")
        return SETUP_NAME
    if name in _UI_BUTTONS:
        await _edit_setup_msg(ctx, chat_id, tg_id, "That's a keyboard button 🙂 Please <b>type</b> your name:", parse_mode="HTML")
        return SETUP_NAME
    s = await _get_setup_data(tg_id)
    d = s["data"]
    d["name"] = name
    _save_setup_data(tg_id, data=d)
    await _edit_setup_msg(ctx, chat_id, tg_id, f"Nice, <b>{esc(name)}</b>! How old are you?", parse_mode="HTML")
    return SETUP_AGE


async def setup_age(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id
    try:
        await update.message.delete()
    except Exception:
        pass
    try:
        age = int(update.message.text.strip())
    except ValueError:
        await _edit_setup_msg(ctx, chat_id, tg_id, "Please enter a valid age (number):")
        return SETUP_AGE
    if age < 18 or age > 80:
        await _edit_setup_msg(ctx, chat_id, tg_id, "Age must be between 18 and 80:")
        return SETUP_AGE
    s = await _get_setup_data(tg_id)
    d = s["data"]
    d["age"] = age
    _save_setup_data(tg_id, data=d)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Male", callback_data="sg:male"),
         InlineKeyboardButton("Female", callback_data="sg:female")]
    ])
    await _edit_setup_msg(ctx, chat_id, tg_id, "What is your gender?", reply_markup=keyboard)
    return SETUP_GENDER


async def setup_gender(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(update.effective_user.id)
    gender = query.data.split(":")[1]
    s = await _get_setup_data(tg_id)
    d = s["data"]
    d["gender"] = gender
    _save_setup_data(tg_id, data=d)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Men", callback_data="si:male"),
         InlineKeyboardButton("Women", callback_data="si:female"),
         InlineKeyboardButton("Both", callback_data="si:both")]
    ])
    await query.edit_message_text("Who are you interested in?", reply_markup=keyboard)
    return SETUP_INTERESTED_IN


async def setup_interested_in(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(update.effective_user.id)
    s = await _get_setup_data(tg_id)
    d = s["data"]
    d["interested_in"] = query.data.split(":")[1]
    _save_setup_data(tg_id, data=d)
    await query.edit_message_text("Which city are you in?")
    return SETUP_CITY


async def setup_city(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id
    try:
        await update.message.delete()
    except Exception:
        pass
    city = update.message.text.strip()[:100]
    if len(city) < 2:
        await _edit_setup_msg(ctx, chat_id, tg_id, "Please enter a valid city name:", parse_mode="HTML")
        return SETUP_CITY
    s = await _get_setup_data(tg_id)
    d = s["data"]
    d["city"] = city
    _save_setup_data(tg_id, data=d)
    await _edit_setup_msg(
        ctx, chat_id, tg_id,
        "Write a short <b>bio</b> (min 10 chars):\n\n<i>Example: Love hiking, coffee addict</i>",
        parse_mode="HTML"
    )
    return SETUP_BIO


async def setup_bio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id
    try:
        await update.message.delete()
    except Exception:
        pass
    bio = update.message.text.strip()[:300]
    if len(bio) < 10:
        await _edit_setup_msg(ctx, chat_id, tg_id, "Bio too short (min 10 chars). Try again:", parse_mode="HTML")
        return SETUP_BIO
    s = await _get_setup_data(tg_id)
    d = s["data"]
    d["bio"] = bio
    _save_setup_data(tg_id, data=d)
    await _edit_setup_msg(
        ctx, chat_id, tg_id,
        "Your <b>Instagram or social handle</b>? (e.g. @username)\n\n"
        "<i>Shared only after matching.</i>",
        parse_mode="HTML"
    )
    return SETUP_SOCIAL


async def setup_social(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id
    try:
        await update.message.delete()
    except Exception:
        pass
    social = update.message.text.strip()[:100]
    s = await _get_setup_data(tg_id)
    d = s["data"]
    d["social_handle"] = social
    _save_setup_data(tg_id, data=d)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💚 Dating", callback_data="lf:dating"),
         InlineKeyboardButton("🔥 Sexting", callback_data="lf:sexting")],
        [InlineKeyboardButton("🤝 Relationship", callback_data="lf:relationship"),
         InlineKeyboardButton("❓ Any / Open", callback_data="lf:any")],
    ])
    await _edit_setup_msg(
        ctx, chat_id, tg_id,
        "What are you <b>looking for</b>?",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    return SETUP_LOOKING_FOR


async def setup_looking_for(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """What kind of relationship are you looking for? (inline buttons)."""
    query = update.callback_query
    await query.answer()
    tg_id = str(update.effective_user.id)
    s = await _get_setup_data(tg_id)
    d = s["data"]
    d["looking_for"] = query.data.split(":")[1]
    _save_setup_data(tg_id, data=d)
    await _edit_setup_msg(
        ctx, update.effective_chat.id, tg_id,
        "Almost done! Send your best <b>profile photo</b>\n\n"
        "<i>Make sure your face is clearly visible.</i>",
        parse_mode="HTML",
    )
    return SETUP_PHOTO


async def setup_photo_remind(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User typed text at the photo step — redirect instead of dead-ending."""
    try:
        await update.message.delete()
    except Exception:
        pass
    tg_id = str(update.effective_user.id)
    await _edit_setup_msg(
        ctx, update.effective_chat.id, tg_id,
        "📷 That was text — please send a <b>photo</b> to finish your profile:",
        parse_mode="HTML",
    )
    return SETUP_PHOTO


async def setup_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id
    try:
        await update.message.delete()
    except Exception:
        pass
    photo = update.message.photo[-1]
    file_id = photo.file_id

    # Re-upload to storage channel if configured (makes file_id permanent)
    try:
        from storage import store_photo_from_file_id
        file_id = await store_photo_from_file_id(ctx.bot, file_id)
    except Exception as e:
        print(f"[SETUP] storage upload failed, using original file_id: {e}")

    s = await _get_setup_data(tg_id)
    d = s["data"]
    name = d.get("name", update.effective_user.first_name or "User")
    age = d.get("age")
    gender = d.get("gender")
    interested_in = d.get("interested_in", "both")
    city = d.get("city", "")
    bio = d.get("bio", "")
    social = d.get("social_handle", "")
    looking_for = d.get("looking_for", "any")

    if not age or not gender:
        sent = await ctx.bot.send_message(chat_id, "Something went wrong. Please use /start to begin again.")
        await _track_bot_message(ctx, sent.message_id)
        return ConversationHandler.END

    lat, lng = 0.0, 0.0
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": city, "format": "json", "limit": 1},
                headers={"User-Agent": "YourMeet/1.0"},
            )
            if resp.status_code == 200:
                dd = resp.json()
                if dd:
                    lat, lng = float(dd[0]["lat"]), float(dd[0]["lon"])
    except Exception:
        pass

    from database import get_conn, row_to_user, USER_COLS
    db = get_conn()
    cols = ", ".join(USER_COLS)
    row = db.execute(f"SELECT {cols} FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
    existing = row_to_user(row)
    photos_json = json.dumps([file_id])

    # In edit mode, preserve the user's approval status so editing a profile
    # doesn't force them back into the review queue — EXCEPT rejected users,
    # whose new profile must go back into the review queue, otherwise they
    # stay rejected forever no matter how many times they edit.
    edit_mode = bool(d.get("_edit_mode"))
    resubmit = edit_mode and bool(existing and existing.is_rejected)

    if existing:
        if edit_mode and not resubmit:
            db.execute(
                """UPDATE users SET name=?, age=?, gender=?, interested_in=?, bio=?, city=?,
                   lat=?, lng=?, social_handle=?, photo=?, photos=?, looking_for=?,
                   terms_accepted=1 WHERE id=?""",
                (name, age, gender, interested_in, bio, city,
                 lat, lng, social, file_id, photos_json, looking_for, existing.id),
            )
        else:
            db.execute(
                """UPDATE users SET name=?, age=?, gender=?, interested_in=?, bio=?, city=?,
                   lat=?, lng=?, social_handle=?, photo=?, photos=?, looking_for=?,
                   is_approved=0, is_rejected=0, terms_accepted=1 WHERE id=?""",
                (name, age, gender, interested_in, bio, city,
                 lat, lng, social, file_id, photos_json, looking_for, existing.id),
            )
        user_id = existing.id
    else:
        db.execute(
            """INSERT INTO users
               (name, age, gender, interested_in, bio, city, lat, lng,
                social_handle, photo, photos, telegram_id, looking_for, is_approved, terms_accepted)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,1)""",
            (name, age, gender, interested_in, bio, city,
             lat, lng, social, file_id, photos_json, tg_id, looking_for),
        )
        row = db.execute("SELECT id FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
        user_id = row[0]

    db.commit()
    db.close()

    ref_id = d.get("pending_ref") or ctx.user_data.get("pending_ref")
    if ref_id:
        await _handle_referral(tg_id, ref_id, bot=ctx.bot)

    try:
        from admin_bot import send_for_review
        if not edit_mode or resubmit:
            await send_for_review(user_id, name, age, gender, city, file_id)
    except Exception as e:
        print(f"[SETUP] admin notify failed: {e}")

    if resubmit:
        sent = await ctx.bot.send_message(
            chat_id,
            "✅ <b>Profile updated &amp; resubmitted!</b>\n\n"
            "Your new profile is in the review queue — you'll be notified once approved!",
            parse_mode="HTML",
            reply_markup=_main_keyboard(),
        )
    elif edit_mode:
        sent = await ctx.bot.send_message(
            chat_id,
            f"✅ <b>Profile updated!</b>\n\nYour changes are live. Approval status preserved.",
            parse_mode="HTML",
            reply_markup=_main_keyboard(),
        )
    else:
        sent = await ctx.bot.send_message(
            chat_id,
            "<b>Profile submitted!</b>\n\n"
            "Our team will review your profile within a few hours.\n"
            "You will get a notification once approved!\n\n"
            "Use /help to see all commands.",
            parse_mode="HTML",
            reply_markup=_main_keyboard(),
        )
    await _track_bot_message(ctx, sent.message_id)
    return ConversationHandler.END


# ── Browse / Swipe ────────────────────────────────────────────────────────────

async def cmd_browse(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    try:
        await update.message.delete()
    except Exception:
        pass
    user = _get_user(tg_id)
    lang = (user.language if user else _lang(update)) or "en"

    if not user or not user.photo:
        await _cleanup_chat(ctx, update.effective_chat.id)
        sent = await update.message.reply_text("Complete your profile first with /start")
        await _track_bot_message(ctx, sent.message_id)
        return
    if not user.is_approved:
        await _cleanup_chat(ctx, update.effective_chat.id)
        sent = await update.message.reply_text("⏳ Your profile is pending approval. We'll notify you soon!")
        await _track_bot_message(ctx, sent.message_id)
        return

    await _send_next_profile(update.message, user, ctx)


async def _send_next_profile(message, user, ctx=None):
    from database import get_conn, row_to_user, USER_COLS
    db = get_conn()

    if ctx:
        await _cleanup_chat(ctx, message.chat_id)

    # Reset swipes if new day
    from datetime import date
    today = str(date.today())
    if getattr(user, "swipes_reset_date", "") != today:
        limit = 999999 if user.is_premium else 30
        db.execute(
            "UPDATE users SET daily_swipes=?, super_likes_left=1, swipes_reset_date=? WHERE id=?",
            (limit, today, user.id)
        )
        db.commit()
        user.__dict__["daily_swipes"] = limit
        user.__dict__["super_likes_left"] = 1

    if not user.is_premium and user.daily_swipes <= 0:
        db.close()
        sent = await message.reply_text(
            "😔 You've used all your swipes for today!\n\n"
            "👑 Upgrade to Premium for unlimited swipes with /premium"
        )
        if ctx:
            ctx.user_data["last_keyboard_msg_id"] = sent.message_id
            await _track_bot_message(ctx, sent.message_id)
        return

    import math
    cols = ", ".join(USER_COLS)
    liked = [r[0] for r in db.execute("SELECT to_user FROM likes WHERE from_user=?", (user.id,)).fetchall()]
    skipped = [r[0] for r in db.execute("SELECT skipped_id FROM skips WHERE user_id=?", (user.id,)).fetchall()]
    blocked = [r[0] for r in db.execute("SELECT blocked_id FROM user_blocks WHERE blocker_id=?", (user.id,)).fetchall()]
    # Also hide profiles of users who blocked ME — they must not be swipable.
    blocked_me = [r[0] for r in db.execute("SELECT blocker_id FROM user_blocks WHERE blocked_id=?", (user.id,)).fetchall()]
    excluded = list(set(liked + skipped + blocked + blocked_me + [user.id]))
    placeholders = ",".join("?" * len(excluded))

    interested_in = getattr(user, "interested_in", "both") or "both"
    if interested_in == "both":
        gender_sql = "gender IN ('male','female')"
        gender_params = ()
    else:
        gender_sql = "gender=?"
        gender_params = (interested_in,)

    min_age = getattr(user, "min_age", 0) or 0
    max_age = getattr(user, "max_age", 0) or 0
    max_distance = getattr(user, "max_distance", 0) or 0
    age_sql = "AND age >= 18"
    age_params = ()
    if min_age and max_age:
        age_sql = "AND age >= ? AND age <= ?"
        age_params = (min_age, max_age)
    elif min_age:
        age_sql = "AND age >= ?"
        age_params = (min_age,)
    elif max_age:
        age_sql = "AND age <= ?"
        age_params = (max_age,)

    # When a distance filter is set, candidates are rejected in Python —
    # a LIMIT 10 page can die entirely to far-away profiles, so widen it.
    fetch_limit = 50 if max_distance else 10
    # Reciprocity tiering: boosted > already-likes-you > everyone else.
    # People who liked you surfacing first directly raises match rate.
    rows = db.execute(
        f"""SELECT {cols} FROM users
            WHERE id NOT IN ({placeholders})
            AND {gender_sql} {age_sql}
            AND is_blocked=0 AND is_rejected=0 AND is_approved=1
            ORDER BY CASE
                WHEN boosted_until > datetime('now') THEN 0
                WHEN id IN (SELECT from_user FROM likes WHERE to_user=?) THEN 1
                ELSE 2 END, RANDOM()
            LIMIT ?""",
        (*excluded, *gender_params, *age_params, user.id, fetch_limit),
    ).fetchall()
    liked_me = {r[0] for r in db.execute(
        "SELECT from_user FROM likes WHERE to_user=?", (user.id,)
    ).fetchall()}
    db.close()

    # Apply distance filter in Python
    profile = None
    for r in rows:
        candidate = row_to_user(r)
        if max_distance and getattr(user, "lat", 0) and getattr(candidate, "lat", 0):
            lat1, lng1 = user.lat, user.lng
            lat2, lng2 = candidate.lat, candidate.lng
            p1, p2 = math.radians(lat1), math.radians(lat2)
            a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
            if 2 * 6371.0 * math.asin(math.sqrt(a)) > max_distance:
                continue
        profile = candidate
        break

    if not profile:
        # Social proof: "app khali hai" wali feeling is the #1 churn reason
        # for tiny apps — show recent growth if there is any.
        from database import get_conn
        ndb = get_conn()
        try:
            row = ndb.execute(
                "SELECT COUNT(*) FROM users WHERE is_approved=1 AND created_at >= datetime('now','-7 days')"
            ).fetchone()
            new_count = row[0] if row else 0
        except Exception:
            new_count = 0
        finally:
            ndb.close()
        extra = f"\n\n✨ <b>{new_count} new people joined this week!</b> They'll show up here soon." if new_count else ""
        sent = await message.reply_text(
            f"😔 No more profiles right now!{extra}\n\n"
            "Check back later — or invite friends with /share 💕",
            parse_mode="HTML",
        )
        if ctx:
            ctx.user_data["last_keyboard_msg_id"] = sent.message_id
            await _track_bot_message(ctx, sent.message_id)
        return

    super_left = getattr(user, "super_likes_left", 1)

    try:
        interests = json.loads(profile.interests or "[]")
    except Exception:
        interests = []

    caption = (
        f"<b>{esc(profile.name)}, {profile.age}</b> 📍 {esc(profile.city) or '-'}\n\n"
        f"{esc(profile.bio or '')}\n"
    )
    lf = getattr(profile, "looking_for", "any") or "any"
    lf_label = {"dating": "💚 Dating", "sexting": "🔥 Sexting",
                "relationship": "🤝 Relationship", "any": "❓ Any"}.get(lf, lf)
    caption += f"\n🎯 Looking for: {lf_label}"
    if interests:
        caption += f"\n🏷 {' · '.join(esc(i) for i in interests[:5])}"
    if profile.is_verified:
        caption += "\n✅ Verified"
    if profile.id in liked_me:
        caption += "\n💙 <i>Already likes you — like back for an instant match!</i>"

    keyboard = _browse_keyboard(profile.id, super_left)

    # Mystery Mode (Premium): hide the photo while active.
    from datetime import datetime as _dt
    mystery_active = False
    try:
        mu = getattr(profile, "mystery_until", "") or ""
        mystery_active = bool(mu) and _dt.strptime(mu, "%Y-%m-%d %H:%M:%S") > _dt.utcnow()
    except ValueError:
        mystery_active = False

    try:
        if mystery_active:
            sent = await message.reply_text(
                caption + "\n\n🙈 <i>Mystery Mode — photo hidden</i>",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        else:
            sent = await message.reply_photo(
                photo=profile.photo,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        if ctx:
            ctx.user_data["last_keyboard_msg_id"] = sent.message_id
            await _track_bot_message(ctx, sent.message_id)
    except Exception:
        sent = await message.reply_text(
            caption + "\n\n<i>(Photo unavailable)</i>",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        if ctx:
            ctx.user_data["last_keyboard_msg_id"] = sent.message_id
            await _track_bot_message(ctx, sent.message_id)


async def cb_like(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("❤️ Liked!")
    target_id = int(query.data.split(":")[1])
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    if not user:
        return

    matched = await _do_like(user, target_id, is_super=False)
    # Re-fetch user so daily_swipes count is fresh for next profile
    user = _get_user(tg_id)
    if matched:
        from database import get_conn, row_to_user, USER_COLS
        db = get_conn()
        cols = ", ".join(USER_COLS)
        target = row_to_user(db.execute(f"SELECT {cols} FROM users WHERE id=?", (target_id,)).fetchone())
        db.close()
        if target:
            sent = await query.message.reply_text(
                f"🎉 <b>It's a Match!</b>\n\nYou and <b>{esc(target.name)}</b> liked each other! 💕\n\n"
                f"Start chatting — just send a message here!{_match_extra_text(user, target)}",
                parse_mode="HTML"
            )
            await _track_bot_message(ctx, sent.message_id)
    await _send_next_profile(query.message, user, ctx)


async def cb_superlike(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    if not user:
        return
    if user.super_likes_left <= 0:
        await query.answer("No super likes left today!", show_alert=True)
        return
    await query.answer("⭐ Super Liked!")
    target_id = int(query.data.split(":")[1])
    matched = await _do_like(user, target_id, is_super=True)
    # Re-fetch user so super_likes_left count is fresh for next profile
    user = _get_user(tg_id)
    if matched:
        from database import get_conn, row_to_user, USER_COLS
        db = get_conn()
        cols = ", ".join(USER_COLS)
        target = row_to_user(db.execute(f"SELECT {cols} FROM users WHERE id=?", (target_id,)).fetchone())
        db.close()
        if target:
            sent = await query.message.reply_text(
                f"🎉 <b>It's a Match!</b>\n\nYou and <b>{esc(target.name)}</b> liked each other! 💕"
                f"{_match_extra_text(user, target)}",
                parse_mode="HTML"
            )
            await _track_bot_message(ctx, sent.message_id)
    await _send_next_profile(query.message, user, ctx)


REPORT_REASONS = ["fake_profile", "spam", "underage", "harassment", "inappropriate", "other"]
_REPORT_LABELS = {
    "fake_profile": "👤 Fake profile",
    "spam": "📢 Spam",
    "underage": "🔞 Underage",
    "harassment": "😠 Harassment",
    "inappropriate": "🚫 Inappropriate",
    "other": "❓ Other",
}


async def cb_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """⚠️ Report button on a browse card — show the reason picker."""
    query = update.callback_query
    await query.answer()
    target_id = int(query.data.split(":")[1])
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    if not user or target_id == user.id:
        return
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"reportreason:{target_id}:{reason}")]
        for reason, label in _REPORT_LABELS.items()
    ])
    sent = await query.message.reply_text(
        "🚨 Why are you reporting this profile?",
        reply_markup=keyboard,
    )
    await _track_bot_message(ctx, sent.message_id)


async def cb_report_reason(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Reason picked — save report, auto-ban at 3+, ping the admin bot."""
    query = update.callback_query
    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer()
        return
    target_id = int(parts[1])
    reason = parts[2]
    if reason not in REPORT_REASONS:
        await query.answer()
        return
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    if not user or target_id == user.id:
        await query.answer()
        return
    if not ratelimit.allow(f"report:{user.id}", 5, 60):
        await query.answer("⏳ Too many reports — please slow down.", show_alert=True)
        return

    from database import get_conn, log_audit
    db = get_conn()
    already = db.execute(
        "SELECT id FROM reports WHERE reporter_id=? AND reported_id=?",
        (user.id, target_id),
    ).fetchone()
    if already:
        db.close()
        await query.answer("You already reported this profile.", show_alert=True)
        return
    db.execute(
        "INSERT INTO reports (reporter_id, reported_id, reason) VALUES (?,?,?)",
        (user.id, target_id, reason),
    )
    db.commit()
    row = db.execute("SELECT COUNT(*) FROM reports WHERE reported_id=?", (target_id,)).fetchone()
    count = row[0] if row else 0
    flagged = False
    if count >= 3:
        # Hide from the feed and hand to admin review instead of an instant
        # ban — three coordinated fake reports must not permanently ban an
        # innocent user (brigading). Admin approves or bans via /user <id>.
        db.execute("UPDATE users SET is_approved=0 WHERE id=?", (target_id,))
        db.commit()
        flagged = True
    trow = db.execute("SELECT name FROM users WHERE id=?", (target_id,)).fetchone()
    db.close()
    log_audit(tg_id, "report_user", target_id, reason)

    await query.answer("✅ Report sent — thank you for keeping YourMeet safe!")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await _notify_admin_report(
        ctx.bot, user, target_id, (trow[0] if trow else "?"), reason, count, flagged
    )


async def _notify_admin_report(bot, reporter, target_id, target_name, reason, count, flagged_for_review):
    """Best-effort ping to the admin chat about a new report."""
    admin_tg_id = os.getenv("ADMIN_TG_ID", "").strip()
    admin_token = os.getenv("ADMIN_BOT_TOKEN", "").strip().strip("'\"")
    if not admin_tg_id or not admin_token:
        return
    label = _REPORT_LABELS.get(reason, reason)
    text = (
        f"🚨 <b>New Report</b>\n\n"
        f"👤 Reported: #{target_id} {esc(target_name)}\n"
        f"❓ Reason: {label}\n"
        f"🙋 By: #{reporter.id} {esc(reporter.name)}\n"
        f"📊 Total reports on this user: {count}"
    )
    if flagged_for_review:
        text += (
            "\n\n⚠️ <b>Profile HIDDEN from feed (3+ reports) — REVIEW NEEDED.</b>\n"
            "Approve or ban via /user <id> — no auto-ban, fake report brigades "
            "must not be able to ban innocent users."
        )
    try:
        from telegram import Bot
        await Bot(token=admin_token).send_message(chat_id=admin_tg_id, text=text, parse_mode="HTML")
    except Exception as e:
        print(f"[BOT] admin report notify failed: {e}")


async def cb_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("👎 Skipped")
    target_id = int(query.data.split(":")[1])
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    if not user or target_id == 0:
        return
    from database import get_conn
    db = get_conn()
    db.execute("INSERT OR IGNORE INTO skips (user_id, skipped_id) VALUES (?,?)", (user.id, target_id))
    db.commit()
    db.close()
    # Re-fetch so swipe counts are fresh
    user = _get_user(tg_id)
    await _send_next_profile(query.message, user, ctx)


async def cb_next(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)  # always fresh fetch
    if not user:
        return
    if not user.is_approved:
        await query.answer("⏳ Profile pending approval.", show_alert=True)
        return
    await _send_next_profile(query.message, user, ctx)


def _match_extra_text(recipient, other) -> str:
    """Premium recipients get their match's social handle.

    The setup flow promises users their handle is 'Shared only after
    matching' and premium_info advertises 'Contact details on match' —
    but the bot never actually sent it (only a dead HTTP endpoint did).
    """
    handle = (getattr(other, "social_handle", "") or "").strip()
    if recipient.is_premium and handle:
        return f"\n\n📞 <b>{esc(other.name)}'s social:</b> {esc(handle)}"
    return ""


async def _do_like(user, target_id: int, is_super: bool) -> bool:
    from database import get_conn, row_to_user, USER_COLS, DuplicateError
    db = get_conn()

    # Serialize the like→match→chat sequence so concurrent requests cannot
    # create duplicate likes / matches / chat sessions.
    try:
        db.begin()
    except Exception:
        pass

    try:
        if not user.is_premium:
            row = db.execute("SELECT daily_swipes, super_likes_left FROM users WHERE id=?", (user.id,)).fetchone()
            swipes = row[0] if row else 0
            super_left = row[1] if row else 0
            if swipes <= 0:
                db.rollback()
                return False
            if is_super and super_left <= 0:
                db.rollback()
                return False

        # INSERT the like; a UNIQUE violation means another request already
        # recorded this like — that's fine, treat it as already-liked.
        already = db.execute(
            "SELECT id FROM likes WHERE from_user=? AND to_user=?", (user.id, target_id)
        ).fetchone()
        liked_new = already is None
        if liked_new:
            try:
                db.execute(
                    "INSERT INTO likes (from_user, to_user, is_super) VALUES (?,?,?)",
                    (user.id, target_id, int(is_super)),
                )
                if not user.is_premium:
                    db.execute("UPDATE users SET daily_swipes=daily_swipes-1 WHERE id=?", (user.id,))
                    if is_super:
                        db.execute("UPDATE users SET super_likes_left=super_likes_left-1 WHERE id=?", (user.id,))
            except DuplicateError:
                pass  # concurrent duplicate — benign, continue

        # Don't notify the target if they blocked the liker.
        target_blocked_liker = db.execute(
            "SELECT 1 FROM user_blocks WHERE blocker_id=? AND blocked_id=?",
            (target_id, user.id),
        ).fetchone() is not None

        if target_blocked_liker:
            # A block must be final: drop the target's stale like toward the
            # liker so it can never produce a new match or chat session later.
            db.execute(
                "DELETE FROM likes WHERE from_user=? AND to_user=?",
                (target_id, user.id),
            )

        mutual = None if target_blocked_liker else db.execute(
            "SELECT id FROM likes WHERE from_user=? AND to_user=?", (target_id, user.id)
        ).fetchone()

        match_id = None
        new_match = False
        if mutual:
            existing_match = db.execute(
                "SELECT id FROM matches WHERE (user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?)",
                (user.id, target_id, target_id, user.id),
            ).fetchone()
            if existing_match:
                match_id = existing_match[0]
            else:
                try:
                    db.execute("INSERT INTO matches (user1_id, user2_id) VALUES (?,?)", (user.id, target_id))
                    match_row = db.execute(
                        "SELECT id FROM matches WHERE (user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?)",
                        (user.id, target_id, target_id, user.id)
                    ).fetchone()
                    match_id = match_row[0] if match_row else None
                    new_match = match_id is not None
                except DuplicateError:
                    # Concurrent request created the match — it will notify; don't.
                    match_row = db.execute(
                        "SELECT id FROM matches WHERE (user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?)",
                        (user.id, target_id, target_id, user.id)
                    ).fetchone()
                    match_id = match_row[0] if match_row else None
        db.commit()
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        print(f"[LIKE] transaction failed: {e}")
        db.close()
        return False

    cols = ", ".join(USER_COLS)
    target = row_to_user(db.execute(f"SELECT {cols} FROM users WHERE id=?", (target_id,)).fetchone())
    db.close()

    if match_id and target and not new_match:
        # Re-like of an already-matched profile (old card, /likes Like-back):
        # stay silent — re-sending match notifications was a spam vector.
        return False

    if match_id and target:
        try:
            from routers.chat import _start_chat_session
            from database import get_conn as gc
            chat_db = gc()
            await _start_chat_session(chat_db, match_id, user, target)
            chat_db.close()
        except Exception as e:
            print(f"[LIKE] chat session failed: {e}")
        try:
            from main import bot_app
            from routers.vibe import send_vibe_question_to_match
            if bot_app and target and target.telegram_id:
                await notify_match(bot_app.bot, target, user)
            if bot_app and match_id and target:
                await send_vibe_question_to_match(bot_app.bot, match_id, user, target)
        except Exception as e:
            print(f"[LIKE] notify failed: {e}")
        return True

    # One-sided like — notify the target (contact details hidden for free users).
    if liked_new and target and target.telegram_id and not target_blocked_liker:
        try:
            from main import bot_app
            if bot_app and target.is_premium:
                await notify_like(bot_app.bot, target, user, premium=True, is_super=is_super)
            else:
                await notify_like(bot_app.bot, target, user, premium=False, is_super=is_super)
        except Exception as e:
            print(f"[LIKE] notify_like failed: {e}")
    return False


# ── Commands ──────────────────────────────────────────────────────────────────

async def cmd_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    try:
        await update.message.delete()
    except Exception:
        pass
    user = _get_user(tg_id)
    lang = (user.language if user else _lang(update)) or "en"
    if not user or not user.photo:
        await _cleanup_chat(ctx, update.effective_chat.id)
        sent = await update.message.reply_text("No profile yet. Use /start to create one.")
        await _track_bot_message(ctx, sent.message_id)
        return
    premium = "✅" if user.is_premium else "❌"
    status = "Approved ✅" if user.is_approved else ("Rejected ❌" if user.is_rejected else "Pending ⏳")
    looking_for = getattr(user, "looking_for", "any") or "any"
    lf_label = {"dating": "💚 Dating", "sexting": "🔥 Sexting",
                "relationship": "🤝 Relationship", "any": "❓ Any"}.get(looking_for, looking_for)
    text = s(lang, "your_profile", name=esc(user.name), age=user.age,
             gender=user.gender or "-", city=esc(user.city) or "-",
             looking_for=lf_label,
             premium=premium, status=status)
    if user.is_premium and getattr(user, "premium_until", ""):
        text += f"\n\n📅 Premium valid till: {esc(user.premium_until[:10])}"
    await _cleanup_chat(ctx, update.effective_chat.id)
    keyboard = _main_keyboard()
    sent = await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    ctx.user_data["last_keyboard_msg_id"] = sent.message_id
    await _track_bot_message(ctx, sent.message_id)


async def cmd_matches(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id
    try:
        await update.message.delete()
    except Exception:
        pass
    user = _get_user(tg_id)
    lang = (user.language if user else _lang(update)) or "en"
    if not user:
        await _cleanup_chat(ctx, chat_id)
        sent = await ctx.bot.send_message(chat_id, "No profile yet. Use /start to create one.")
        await _track_bot_message(ctx, sent.message_id)
        return
    from database import get_conn, row_to_user, USER_COLS
    db = get_conn()
    cols = ", ".join(USER_COLS)
    rows = db.execute(
        "SELECT id, user1_id, user2_id FROM matches WHERE user1_id=? OR user2_id=? ORDER BY matched_at DESC LIMIT 10",
        (user.id, user.id)
    ).fetchall()
    db.close()
    if not rows:
        await _cleanup_chat(ctx, chat_id)
        sent = await ctx.bot.send_message(chat_id, "💔 No matches yet. Use /browse to find people!")
        await _track_bot_message(ctx, sent.message_id)
        return
    text = f"💕 <b>Your Matches ({len(rows)})</b>\n\n"
    buttons = []
    from database import get_conn as gc
    db2 = gc()
    for match_id, u1, u2 in rows:
        other_id = u2 if u1 == user.id else u1
        other_row = db2.execute(f"SELECT {cols} FROM users WHERE id=?", (other_id,)).fetchone()
        if other_row:
            other = row_to_user(other_row)
            text += f"• <b>{esc(other.name)}</b>, {other.age} — {esc(other.city) or '-'}\n"
            # Short labels: the list text above already shows the names.
            buttons.append([
                InlineKeyboardButton("💬 Chat", callback_data=f"chat_match:{match_id}"),
                InlineKeyboardButton("💔 Unmatch", callback_data=f"unmatch:{match_id}"),
                InlineKeyboardButton("🚫 Block", callback_data=f"blockuser:{other_id}"),
            ])
    db2.close()
    await _cleanup_chat(ctx, chat_id)
    reply_markup = InlineKeyboardMarkup(buttons) if buttons else _main_keyboard()
    sent = await ctx.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=reply_markup)
    ctx.user_data["last_keyboard_msg_id"] = sent.message_id
    await _track_bot_message(ctx, sent.message_id)


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    try:
        await update.message.delete()
    except Exception:
        pass
    user = _get_user(tg_id)
    if not user:
        await _cleanup_chat(ctx, update.effective_chat.id)
        sent = await update.message.reply_text("No profile yet. Use /start to create one.")
        await _track_bot_message(ctx, sent.message_id)
        return
    from database import get_conn
    db = get_conn()
    given = db.execute("SELECT COUNT(*) FROM likes WHERE from_user=?", (user.id,)).fetchone()[0]
    received = db.execute("SELECT COUNT(*) FROM likes WHERE to_user=?", (user.id,)).fetchone()[0]
    matches = db.execute(
        "SELECT COUNT(*) FROM matches WHERE user1_id=? OR user2_id=?", (user.id, user.id)
    ).fetchone()[0]
    db.close()
    lang = user.language or "en"
    swipes_display = "∞" if user.is_premium else user.daily_swipes
    await _cleanup_chat(ctx, update.effective_chat.id)
    sent = await update.message.reply_text(
        s(lang, "your_stats", given=given, received=received,
          matches=matches, swipes=swipes_display),
        parse_mode="HTML",
        reply_markup=_main_keyboard()
    )
    ctx.user_data["last_keyboard_msg_id"] = sent.message_id
    await _track_bot_message(ctx, sent.message_id)


async def cmd_premium(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    try:
        await update.message.delete()
    except Exception:
        pass
    user = _get_user(tg_id)
    lang = (user.language if user else _lang(update)) or "en"
    await _cleanup_chat(ctx, update.effective_chat.id)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("1 Month — 150 ⭐", callback_data="buy:monthly")],
        [InlineKeyboardButton("3 Months — 350 ⭐", callback_data="buy:quarterly")],
    ])
    sent = await update.message.reply_text(s(lang, "premium_info"), parse_mode="HTML", reply_markup=keyboard)
    ctx.user_data["last_keyboard_msg_id"] = sent.message_id
    await _track_bot_message(ctx, sent.message_id)


async def cmd_share(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    try:
        await update.message.delete()
    except Exception:
        pass
    bot_username = os.getenv("BOT_USERNAME", "").strip().strip("'\"")
    if not bot_username:
        try:
            bot_info = await ctx.bot.get_me()
            bot_username = bot_info.username or ""
        except Exception:
            pass
    if not bot_username:
        await _cleanup_chat(ctx, update.effective_chat.id)
        sent = await update.message.reply_text("❌ Could not generate referral link.")
        await _track_bot_message(ctx, sent.message_id)
        return
    user = _get_user(tg_id)
    lang = (user.language if user else _lang(update)) or "en"
    link = f"https://t.me/{bot_username}?start=ref_{tg_id}"
    await _cleanup_chat(ctx, update.effective_chat.id)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Share Link", url=link)],
    ])
    sent = await update.message.reply_text(s(lang, "referral_msg", link=link), reply_markup=keyboard)
    ctx.user_data["last_keyboard_msg_id"] = sent.message_id
    await _track_bot_message(ctx, sent.message_id)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.delete()
    except Exception:
        pass
    await _cleanup_chat(ctx, update.effective_chat.id)
    sent = await update.message.reply_text(
        "📖 <b>YourMeet Commands</b>\n\n"
        "/likes — See who liked you (Premium) ⭐\n"
        "/mystery — Hide your photo 24h (Premium) 🙈\n\n"
        "Tap a button below, or type any command.",
        parse_mode="HTML",
        reply_markup=_main_keyboard(),
    )
    ctx.user_data["last_keyboard_msg_id"] = sent.message_id
    await _track_bot_message(ctx, sent.message_id)


async def cmd_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.delete()
    except Exception:
        pass
    await _cleanup_chat(ctx, update.effective_chat.id)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes, Delete", callback_data="cmd:confirmdelete")],
    ])
    sent = await update.message.reply_text(
        "⚠️ Are you sure you want to delete your account?\n\n"
        "This will permanently delete all your data, matches and messages.\n\n"
        "Type /confirmdelete to confirm, or tap below.",
        reply_markup=keyboard
    )
    ctx.user_data["last_keyboard_msg_id"] = sent.message_id
    await _track_bot_message(ctx, sent.message_id)


async def cmd_confirm_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    if user:
        from database import get_conn
        from routers.auth import _delete_user_data
        db = get_conn()
        _delete_user_data(db, user.id)
        db.close()
    await _cleanup_chat(ctx, update.effective_chat.id)
    sent = await update.message.reply_text("✅ Your account has been deleted. Goodbye! 👋")
    await _track_bot_message(ctx, sent.message_id)


async def cmd_language(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.delete()
    except Exception:
        pass
    langs = [
        ("🇬🇧 English", "en"), ("🇪🇸 Español", "es"), ("🇷🇺 Русский", "ru"),
        ("🇰🇷 한국어", "ko"), ("🇨🇳 中文", "zh"), ("🇮🇩 Indonesia", "id"),
        ("🇸🇦 العربية", "ar"), ("🇧🇷 Português", "pt"), ("🇫🇷 Français", "fr"),
        ("🇩🇪 Deutsch", "de"), ("🇹🇷 Türkçe", "tr"), ("🇮🇹 Italiano", "it"),
        ("🇯🇵 日本語", "ja"), ("🇮🇳 हिंदी", "hi"),
    ]
    buttons = [[InlineKeyboardButton(name, callback_data=f"lang:{code}")] for name, code in langs]
    await _cleanup_chat(ctx, update.effective_chat.id)
    sent = await update.message.reply_text(
        "🌐 Choose your language:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    ctx.user_data["last_keyboard_msg_id"] = sent.message_id
    await _track_bot_message(ctx, sent.message_id)


async def cmd_boost(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    try:
        await update.message.delete()
    except Exception:
        pass
    user = _get_user(tg_id)
    lang = (user.language if user else _lang(update)) or "en"
    if not user or not user.is_premium:
        await _cleanup_chat(ctx, update.effective_chat.id)
        sent = await update.message.reply_text("👑 Boost is a Premium feature. Use /premium to upgrade!")
        await _track_bot_message(ctx, sent.message_id)
        return
    from datetime import datetime, timedelta
    from database import get_conn
    db = get_conn()
    until = (datetime.utcnow() + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE users SET boosted_until=? WHERE id=?", (until, user.id))
    db.commit()
    db.close()
    await _cleanup_chat(ctx, update.effective_chat.id)
    keyboard = _main_keyboard()
    sent = await update.message.reply_text(s(lang, "boost_active"), parse_mode="HTML", reply_markup=keyboard)
    ctx.user_data["last_keyboard_msg_id"] = sent.message_id
    await _track_bot_message(ctx, sent.message_id)


async def cmd_mystery(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Premium only — toggle Mystery Mode for 24h (hides photo in browse).

    The display side lives in _send_next_profile; the old toggle was an
    HTTP-only endpoint that died with the mini app.
    """
    tg_id = str(update.effective_user.id)
    try:
        await update.message.delete()
    except Exception:
        pass
    user = _get_user(tg_id)
    if not user or not user.is_premium:
        sent = await update.message.reply_text("🙈 Mystery Mode is a Premium feature. Use /premium to upgrade!")
        return
    from datetime import datetime as _dt, timedelta
    from database import get_conn
    db = get_conn()
    now = _dt.utcnow()
    current = getattr(user, "mystery_until", "") or ""
    active = False
    try:
        active = bool(current) and _dt.strptime(current, "%Y-%m-%d %H:%M:%S") > now
    except ValueError:
        active = False
    if active:
        db.execute("UPDATE users SET mystery_until='' WHERE id=?", (user.id,))
        msg = "🙈 <b>Mystery Mode OFF.</b>\n\nYour photo is visible in browse again."
    else:
        until = (now + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        db.execute("UPDATE users SET mystery_until=? WHERE id=?", (until, user.id))
        msg = (
            "🙈 <b>Mystery Mode ON for 24 hours!</b>\n\n"
            "Your photo is now hidden in browse — people still see your name, age and bio.\n"
            "Run /mystery again to turn it off."
        )
    db.commit()
    db.close()
    await _cleanup_chat(ctx, update.effective_chat.id)
    sent = await update.message.reply_text(msg, parse_mode="HTML", reply_markup=_main_keyboard())
    ctx.user_data["last_keyboard_msg_id"] = sent.message_id
    await _track_bot_message(ctx, sent.message_id)


async def cmd_likes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Premium only — see who liked you, with a Like-back button per user.

    The old endpoint was HTTP-only and died with the mini app; this restores
    the advertised 'See who liked you' premium benefit in bot-only mode.
    """
    tg_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id
    try:
        await update.message.delete()
    except Exception:
        pass
    user = _get_user(tg_id)
    if not user or not user.is_premium:
        sent = await update.message.reply_text("⭐ Who liked you is a Premium feature. Use /premium to upgrade!")
        return
    from database import get_conn, row_to_user, USER_COLS
    cols = ", ".join(USER_COLS)
    db = get_conn()
    rows = db.execute(
        """SELECT l.from_user, l.is_super FROM likes l
           WHERE l.to_user=?
           AND l.from_user NOT IN (SELECT blocked_id FROM user_blocks WHERE blocker_id=?)
           AND l.from_user NOT IN (SELECT blocker_id FROM user_blocks WHERE blocked_id=?)
           ORDER BY l.is_super DESC, l.id DESC LIMIT 10""",
        (user.id, user.id, user.id),
    ).fetchall()
    db.close()
    if not rows:
        await _cleanup_chat(ctx, chat_id)
        sent = await update.message.reply_text(
            "💔 No likes yet — keep browsing with /browse!",
            reply_markup=_main_keyboard(),
        )
        ctx.user_data["last_keyboard_msg_id"] = sent.message_id
        await _track_bot_message(ctx, sent.message_id)
        return
    text = "⭐ <b>Who Liked You</b>\n\n"
    buttons = []
    db2 = get_conn()
    for from_id, is_super in rows:
        r = db2.execute(f"SELECT {cols} FROM users WHERE id=?", (from_id,)).fetchone()
        u = row_to_user(r)
        if not u:
            continue
        star = " ⭐ SUPER" if is_super else ""
        text += f"• <b>{esc(u.name)}</b>, {u.age} — {esc(u.city) or '-'}{star}\n"
        buttons.append([InlineKeyboardButton(f"❤️ Like back — {u.name}", callback_data=f"like:{u.id}")])
    db2.close()
    await _cleanup_chat(ctx, chat_id)
    markup = InlineKeyboardMarkup(buttons) if buttons else _main_keyboard()
    sent = await ctx.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)
    ctx.user_data["last_keyboard_msg_id"] = sent.message_id
    await _track_bot_message(ctx, sent.message_id)


async def cmd_block(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Block a user by Telegram ID or name search — /block <id>"""
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    lang = (user.language if user else _lang(update)) or "en"
    if not user:
        await update.message.reply_text("No profile yet. Use /start to create one.")
        return
    if not ctx.args:
        await update.message.reply_text(
            "Usage: /block <user_id>\n\n"
            "💡 Tip: open /matches and tap the 🚫 Block button to block a match directly."
        )
        return
    try:
        target_id = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID.")
        return
    if target_id == user.id:
        await update.message.reply_text("You can't block yourself.")
        return
    from database import get_conn
    db = get_conn()
    db.execute("INSERT OR IGNORE INTO user_blocks (blocker_id, blocked_id) VALUES (?,?)", (user.id, target_id))
    db.execute("DELETE FROM matches WHERE (user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?)", (user.id, target_id, target_id, user.id))
    db.commit()
    db.close()
    from database import log_audit
    log_audit(tg_id, "block_user", target_id)
    await update.message.reply_text("🚫 User blocked. They can no longer see your profile or message you.")


async def cmd_filters(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Set age/distance filters — /filters <min_age> <max_age> <max_distance_km>"""
    tg_id = str(update.effective_user.id)
    try:
        await update.message.delete()
    except Exception:
        pass
    user = _get_user(tg_id)
    lang = (user.language if user else _lang(update)) or "en"
    if not user:
        await update.message.reply_text("No profile yet. Use /start to create one.")
        return
    if not ctx.args:
        from database import get_conn
        db = get_conn()
        row = db.execute("SELECT min_age, max_age, max_distance FROM users WHERE id=?", (user.id,)).fetchone()
        db.close()
        ma, xa, md = (row if row else (0, 0, 0))
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("18-25", callback_data="filter:18:25:0"),
                InlineKeyboardButton("25-35", callback_data="filter:25:35:0"),
                InlineKeyboardButton("35-50", callback_data="filter:35:50:0"),
            ],
            [
                InlineKeyboardButton("Within 10 km", callback_data="filter:0:0:10"),
                InlineKeyboardButton("Within 50 km", callback_data="filter:0:0:50"),
                InlineKeyboardButton("Within 100 km", callback_data="filter:0:0:100"),
            ],
            [InlineKeyboardButton("✖️ Clear Filters", callback_data="filter:0:0:0")],
        ])
        await _cleanup_chat(ctx, update.effective_chat.id)
        sent = await update.message.reply_text(
            f"🎯 <b>Your Filters</b>\n\n"
            f"Min age: {ma or 'any'}\n"
            f"Max age: {xa or 'any'}\n"
            f"Max distance: {md or 'any'} km\n\n"
            f"Or type: /filters <min> <max> <km>\nExample: /filters 20 35 50",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        ctx.user_data["last_keyboard_msg_id"] = sent.message_id
        await _track_bot_message(ctx, sent.message_id)
        return
    try:
        min_age = max(0, min(100, int(ctx.args[0])))
        max_age = max(0, min(100, int(ctx.args[1]) if len(ctx.args) > 1 else 0))
        max_distance = max(0, min(500, int(ctx.args[2]) if len(ctx.args) > 2 else 0))
    except ValueError:
        await update.message.reply_text("Usage: /filters <min_age> <max_age> <max_distance_km>")
        return
    from database import get_conn
    db = get_conn()
    db.execute("UPDATE users SET min_age=?, max_age=?, max_distance=? WHERE id=?", (min_age, max_age, max_distance, user.id))
    db.commit()
    db.close()
    await update.message.reply_text(
        f"✅ Filters updated!\n\nMin age: {min_age or 'any'}\nMax age: {max_age or 'any'}\nMax distance: {max_distance or 'any'} km",
        parse_mode="HTML"
    )


async def cmd_edit_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Re-enter setup to edit profile while preserving approval status."""
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    lang = (user.language if user else _lang(update)) or "en"
    if not user:
        await update.message.reply_text("No profile yet. Use /start to create one.")
        return
    await _cleanup_chat(ctx, update.effective_chat.id)
    # Mark that this is an edit session so setup_photo preserves approval.
    _save_setup_data(tg_id, data={"_edit_mode": True})
    m = await update.message.reply_text(
        "✏️ <b>Edit Profile</b>\n\nWhat would you like to change?\n\n"
        "Send your new <b>name</b> (or type /cancel to abort):",
        parse_mode="HTML"
    )
    _save_setup_data(tg_id, msg_id=m.message_id)
    await _track_bot_message(ctx, m.message_id)
    return SETUP_NAME


# ── Callbacks ─────────────────────────────────────────────────────────────────

async def cb_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Dispatch /help and /start inline buttons (cmd:<name>) to commands."""
    query = update.callback_query
    await query.answer()
    cmd = query.data.split(":")[1] if ":" in query.data else ""
    handlers = {
        "profile": cmd_profile, "browse": cmd_browse, "matches": cmd_matches,
        "stats": cmd_stats, "premium": cmd_premium, "share": cmd_share,
        "language": cmd_language, "boost": cmd_boost, "block": cmd_block,
        "filters": cmd_filters, "editprofile": cmd_edit_profile,
        "delete": cmd_delete, "confirmdelete": cmd_confirm_delete,
        "about": cmd_about, "help": cmd_help,
        "mystery": cmd_mystery, "likes": cmd_likes,
    }
    handler = handlers.get(cmd)
    if not handler:
        return
    fake_update = Update.de_json(
        {"update_id": update.update_id, "message": {
            "message_id": query.message.message_id,
            "date": int(__import__("time").time()),
            "chat": {"id": query.message.chat_id, "type": "private"},
            "from": {
                "id": update.effective_user.id,
                "is_bot": False,
                "first_name": update.effective_user.first_name or "User",
            },
            "text": f"/{cmd}",
        }},
        ctx.bot,
    )
    await handler(fake_update, ctx)


async def cb_chat_match(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Tapping 'Chat with X' from matches list — inform user to just send a message."""
    query = update.callback_query
    await query.answer()
    match_id = int(query.data.split(":")[1])
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    if not user:
        return
    from database import get_conn, row_to_user, USER_COLS
    db = get_conn()
    cols = ", ".join(USER_COLS)
    match = db.execute(
        "SELECT user1_id, user2_id FROM matches WHERE id=? AND (user1_id=? OR user2_id=?)",
        (match_id, user.id, user.id)
    ).fetchone()
    if not match:
        db.close()
        await query.answer("Match not found.", show_alert=True)
        return
    other_id = match[1] if match[0] == user.id else match[0]
    other = row_to_user(db.execute(f"SELECT {cols} FROM users WHERE id=?", (other_id,)).fetchone())
    db.close()
    if not other:
        return
    # Pin this match as the message target: forward_message would otherwise
    # always route to the MOST RECENT active session, so with multiple matches
    # "Chat with X" would send messages to the wrong person.
    ctx.user_data["chat_target_tg"] = other.telegram_id
    sent = await query.message.reply_text(
        f"💬 <b>Chatting with {esc(other.name)}</b>\n\n"
        f"Just send your message here and it will be forwarded to {esc(other.name)}!",
        parse_mode="HTML",
        reply_markup=_main_keyboard(),
    )
    await _track_bot_message(ctx, sent.message_id)


async def cb_unmatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("💔 Unmatched")
    match_id = int(query.data.split(":")[1])
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    if not user:
        return
    from database import get_conn
    db = get_conn()
    match = db.execute(
        "SELECT user1_id, user2_id FROM matches WHERE id=? AND (user1_id=? OR user2_id=?)",
        (match_id, user.id, user.id),
    ).fetchone()
    if match:
        other_id = match[1] if match[0] == user.id else match[0]
        db.execute(
            "DELETE FROM matches WHERE id=? AND (user1_id=? OR user2_id=?)",
            (match_id, user.id, user.id),
        )
        # Close only the chat sessions of THIS pair — not the user's other chats.
        db.execute(
            "UPDATE chat_sessions SET is_active=0 WHERE "
            "((user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?)) AND is_active=1",
            (user.id, other_id, other_id, user.id),
        )
    db.commit()
    db.close()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    sent = await query.message.reply_text(
        "✅ Unmatched. They can no longer message you.",
        reply_markup=_main_keyboard()
    )
    await _track_bot_message(ctx, sent.message_id)


async def cb_block_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """🚫 Block button from the matches list — block + unmatch + close chat."""
    query = update.callback_query
    await query.answer("🚫 Blocked")
    target_id = int(query.data.split(":")[1])
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    if not user or target_id == user.id:
        return
    from database import get_conn, log_audit
    db = get_conn()
    db.execute("INSERT OR IGNORE INTO user_blocks (blocker_id, blocked_id) VALUES (?,?)", (user.id, target_id))
    db.execute(
        "DELETE FROM matches WHERE (user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?)",
        (user.id, target_id, target_id, user.id),
    )
    db.execute(
        "UPDATE chat_sessions SET is_active=0 WHERE "
        "((user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?)) AND is_active=1",
        (user.id, target_id, target_id, user.id),
    )
    db.commit()
    db.close()
    log_audit(tg_id, "block_user", target_id)
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    sent = await query.message.reply_text(
        "🚫 User blocked. They can no longer see your profile or message you.",
        reply_markup=_main_keyboard(),
    )
    await _track_bot_message(ctx, sent.message_id)


async def cb_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    plan = query.data.split(":")[1]
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    if not user:
        return
    from routers.payment import PLANS
    if plan not in PLANS:
        return
    p = PLANS[plan]
    await _cleanup_chat(ctx, update.effective_chat.id)
    try:
        await ctx.bot.send_invoice(
            chat_id=tg_id,
            title=p["title"],
            description=p["desc"],
            payload=f"premium:{plan}:{user.id}",
            currency="XTR",
            prices=[{"label": p["title"], "amount": p["stars"]}],
            provider_token="",
        )
    except Exception as e:
        print(f"[BOT] send_invoice failed: {e}")


async def cb_filter(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle /filters quick-pick buttons (filter:min:max:km)."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    if len(parts) != 4:
        return
    _, min_age, max_age, max_distance = parts
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    if not user:
        return
    from database import get_conn
    db = get_conn()
    db.execute(
        "UPDATE users SET min_age=?, max_age=?, max_distance=? WHERE id=?",
        (int(min_age), int(max_age), int(max_distance), user.id),
    )
    db.commit()
    db.close()
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        f"✅ Filters updated!\n\n"
        f"Min age: {min_age or 'any'}\n"
        f"Max age: {max_age or 'any'}\n"
        f"Max distance: {max_distance or 'any'} km",
        parse_mode="HTML",
        reply_markup=_main_keyboard(),
    )


async def cb_vibe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    if len(parts) != 3:
        return
    match_id = int(parts[1])
    answer = parts[2]
    tg_id = str(update.effective_user.id)
    from database import get_conn, row_to_user, USER_COLS
    db = get_conn()
    cols = ", ".join(USER_COLS)
    user = row_to_user(db.execute(f"SELECT {cols} FROM users WHERE telegram_id=?", (tg_id,)).fetchone())
    db.close()
    if not user:
        return
    from routers.vibe import submit_vibe_answer
    vibe_db = get_conn()
    try:
        result = await submit_vibe_answer(match_id, {"answer": answer}, db=vibe_db, current_user=user)
    finally:
        vibe_db.close()
    await query.edit_message_reply_markup(reply_markup=None)
    try:
        import json as _json
        body = _json.loads(result.body) if hasattr(result, "body") else {}
        if body.get("error"):
            sent = await query.message.reply_text("⚠️ Couldn't record your answer — please try again.")
            await _track_bot_message(ctx, sent.message_id)
        elif body.get("already_answered"):
            sent = await query.message.reply_text("✅ You already answered this vibe check!")
            await _track_bot_message(ctx, sent.message_id)
        elif body.get("waiting"):
            sent = await query.message.reply_text("✅ Answer recorded! Waiting for your match...")
            await _track_bot_message(ctx, sent.message_id)
        elif "matched_vibe" in body:
            compat = body.get("compatibility") or {}
            extra = ""
            if compat.get("percent") is not None:
                extra = (
                    f"\n\n💞 Vibe compatibility: <b>{compat['percent']}%</b> "
                    f"({compat['matched']} of {compat['asked']} matched)"
                )
            if body["matched_vibe"]:
                sent = await query.message.reply_text(
                    f"✨ Vibe Match! You both chose: <b>{body.get('my_choice', '')}</b> 💕{extra}",
                    parse_mode="HTML"
                )
                await _track_bot_message(ctx, sent.message_id)
            else:
                sent = await query.message.reply_text(
                    f"🎭 You: <b>{body.get('my_choice', '')}</b> | Match: <b>{body.get('other_choice', '')}</b>{extra}",
                    parse_mode="HTML"
                )
                await _track_bot_message(ctx, sent.message_id)
    except Exception:
        sent = await query.message.reply_text("✅ Answer recorded!")
        await _track_bot_message(ctx, sent.message_id)


async def cb_language(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = query.data.split(":")[1]
    tg_id = str(update.effective_user.id)
    from database import get_conn
    db = get_conn()
    db.execute("UPDATE users SET language=? WHERE telegram_id=?", (lang, tg_id))
    db.commit()
    db.close()
    await query.edit_message_text(s(lang, "language_changed"), parse_mode="HTML")


# ── Payments ──────────────────────────────────────────────────────────────────

async def pre_checkout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    payload = query.invoice_payload or ""
    parts = payload.split(":")
    from routers.payment import PLANS
    if len(parts) != 3 or parts[0] != "premium" or parts[1] not in PLANS:
        await query.answer(ok=False, error_message="Invalid purchase. Please try again with /premium.")
        return
    await query.answer(ok=True)


async def successful_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    payment = update.message.successful_payment
    payload = payment.invoice_payload
    plan = payload.split(":")[1] if payload.count(":") >= 2 else ""
    from routers.payment import PLANS
    # Defense in depth: Telegram echoes the invoice's own payload and amount,
    # but never activate premium unless the paid amount matches the plan price.
    if plan not in PLANS or payment.currency != "XTR" or payment.total_amount != PLANS[plan]["stars"]:
        print(
            f"[PAYMENT] rejected: plan={plan} currency={payment.currency} "
            f"amount={payment.total_amount} expected={PLANS.get(plan, {}).get('stars')}"
        )
        return
    from database import get_conn
    db = get_conn()
    from routers.payment import handle_successful_payment
    await handle_successful_payment(tg_id, payload, db)
    db.close()
    user = _get_user(tg_id)
    lang = (user.language if user else "en") or "en"
    premium_until = user.premium_until[:10] if user and user.premium_until else "-"
    await _cleanup_chat(ctx, update.effective_chat.id)
    sent = await update.message.reply_text(
        s(lang, "premium_activated", date=premium_until),
        parse_mode="HTML"
    )
    await _track_bot_message(ctx, sent.message_id)


# ── Message forwarding ────────────────────────────────────────────────────────

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    text = (update.message.text or "").strip()

    # ── Persistent reply keyboard taps ──
    # Telegram sends the button text as a normal message — route it to the
    # matching command so tapping "🔍 Browse" behaves like /browse.
    button_map = {
        "🔍 Browse": "browse",
        "💕 Matches": "matches",
        "👤 Profile": "profile",
        "📊 Stats": "stats",
        "👑 Premium": "premium",
        "📖 Commands": "help",
        "🌐 Language": "language",
        "🚀 Boost": "boost",
        "🎯 Filters": "filters",
        "✏️ Edit": "editprofile",
        "🚫 Block": "block",
        "🗑 Delete": "delete",
    }
    if text in button_map:
        cmd = button_map[text]
        handlers = {
            "browse": cmd_browse, "matches": cmd_matches, "profile": cmd_profile,
            "stats": cmd_stats, "premium": cmd_premium, "help": cmd_help,
            "language": cmd_language, "boost": cmd_boost, "filters": cmd_filters,
            "editprofile": cmd_edit_profile, "delete": cmd_delete, "block": cmd_block,
        }
        handler = handlers.get(cmd)
        if handler:
            # Delete the keyboard button tap message so chat stays clean
            try:
                await update.message.delete()
            except Exception:
                pass
            fake_update = Update.de_json(
                {"update_id": update.update_id, "message": {
                    "message_id": update.message.message_id,
                    "date": int(__import__("time").time()),
                    "chat": {"id": update.message.chat_id, "type": "private"},
                    "from": {
                        "id": update.effective_user.id,
                        "is_bot": False,
                        "first_name": update.effective_user.first_name or "User",
                    },
                    "text": f"/{cmd}",
                }},
                ctx.bot,
            )
            await handler(fake_update, ctx)
            return

    from database import get_conn
    db = get_conn()
    from routers.chat import forward_message

    # Throttle chat forwarding — without this a user can flood their match
    # (and Telegram's API) with unlimited messages per minute.
    if not ratelimit.allow(f"msg:{tg_id}", 25, 60):
        db.close()
        sent = await update.message.reply_text("⏳ You're sending messages too fast. Please slow down a little!")
        await _track_bot_message(ctx, sent.message_id)
        return
    forwarded = await forward_message(
        tg_id, text, db, preferred_tg=ctx.user_data.get("chat_target_tg")
    )
    db.close()
    if not forwarded:
        await _cleanup_chat(ctx, update.effective_chat.id)
        sent = await update.message.reply_text(
            "💬 No active chat.\n\nUse /matches to see your matches and start chatting!"
        )
        await _track_bot_message(ctx, sent.message_id)


# ── Notify helpers ────────────────────────────────────────────────────────────

async def notify_like(bot, target, liker, premium: bool, is_super: bool = False):
    """Notify a user that someone liked them.

    Free users see name/bio/city but contact details (photo, social handle)
    stay hidden. Premium users see everything. Super likes are clearly
    marked — the whole point of a super like is that the recipient knows.
    """
    if not target.telegram_id:
        return
    lang = (target.language or "en") or "en"
    name = liker.name or "Someone"
    age = liker.age or "-"
    city = liker.city or "-"
    bio = (liker.bio or "")[:120]
    # NOTE: read the is_super PARAMETER — liker._is_super_like was never set
    # anywhere, so the ⭐ mark never rendered and super likes looked identical
    # to normal likes in the notification.
    if is_super:
        if premium:
            text = (
                f"⭐ <b>SUPER LIKE!</b>\n\n"
                f"<b>{esc(name)}, {age}</b> 📍 {esc(city)} super liked your profile!\n\n"
                f"{esc(bio)}\n\n"
                f"They liked you FIRST — open the app and match instantly!"
            )
        else:
            text = (
                f"⭐ <b>SUPER LIKE!</b>\n\n"
                f"<b>{esc(name)}</b> super liked your profile!\n\n"
                f"{esc(bio)}\n"
                f"📍 {esc(city)}\n\n"
                f"🔒 <i>See who and reply — upgrade to Premium.</i>"
            )
    elif premium:
        text = (
            f"🔔 <b>{esc(name)}, {age}</b> 📍 {esc(city)} just liked you!\n\n"
            f"{esc(bio)}\n\n"
            f"Open the app to see who and reply!"
        )
    else:
        text = (
            f"🔔 <b>{esc(name)}</b> just liked you!\n\n"
            f"{esc(bio)}\n"
            f"📍 {esc(city)}\n\n"
            f"🔒 <i>See who and reply — upgrade to Premium.</i>"
        )
    try:
        await bot.send_message(
            chat_id=target.telegram_id,
            text=text,
            parse_mode="HTML",
        )
    except Exception as e:
        print(f"[BOT] notify_like failed: {e}")


async def notify_match(bot, user, matched_with):
    if not user.telegram_id:
        return
    lang = user.language or "en"
    text = s(lang, "match_notify", name=esc(matched_with.name)) + _match_extra_text(user, matched_with)
    try:
        await bot.send_message(
            chat_id=user.telegram_id,
            text=text,
            parse_mode="HTML",
        )
    except Exception as e:
        print(f"[BOT] notify_match failed: {e}")


# ── Referral ──────────────────────────────────────────────────────────────────

async def _handle_referral(new_tg_id: str, referrer_tg_id: str, bot=None):
    """Referral handling.

    Everyone: every 3 completed signups via the link = +10 bonus swipes.
    Admin-gifted users additionally earn premium automatically: when their
    link brings 3 users, they get the SAME number of days the admin gifted
    them (stored in referral_reward_days). Offer amount is admin-controlled.
    """
    if new_tg_id == referrer_tg_id:
        return
    from database import get_conn
    db = get_conn()
    try:
        new_user = _get_user(new_tg_id)
        referrer = _get_user(referrer_tg_id)
        if not new_user or not referrer:
            return
        existing = db.execute(
            "SELECT id FROM referrals WHERE referred_id=?", (new_user.id,)
        ).fetchone()
        if existing:
            return
        db.execute(
            "INSERT INTO referrals (referrer_id, referred_id) VALUES (?,?)",
            (referrer.id, new_user.id)
        )
        db.commit()
        count = db.execute(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (referrer.id,)
        ).fetchone()[0]
        db.execute("UPDATE users SET referral_count=? WHERE id=?", (count, referrer.id))
        rewarded = False
        reward_days = 0
        until = ""
        if count % 3 == 0:
            db.execute("UPDATE users SET daily_swipes=daily_swipes+10 WHERE id=?", (referrer.id,))
            rrow = db.execute(
                "SELECT referral_reward_days FROM users WHERE id=?", (referrer.id,)
            ).fetchone()
            reward_days = (rrow[0] if rrow and rrow[0] else 0)
            if reward_days > 0:
                from datetime import datetime as _dt, timedelta
                base = _dt.utcnow()
                prow = db.execute(
                    "SELECT premium_until FROM users WHERE id=?", (referrer.id,)
                ).fetchone()
                if prow and prow[0]:
                    try:
                        ex = _dt.strptime(prow[0], "%Y-%m-%d %H:%M:%S")
                        if ex > base:
                            base = ex
                    except (ValueError, TypeError):
                        pass
                until = (base + timedelta(days=reward_days)).strftime("%Y-%m-%d %H:%M:%S")
                db.execute(
                    "UPDATE users SET is_premium=1, premium_until=?, super_likes_left=999999, "
                    "daily_swipes=999999 WHERE id=?",
                    (until, referrer.id),
                )
                # One-time offer: consume it so it can never fire again.
                # (Admin re-gifting later creates a fresh offer.)
                db.execute(
                    "UPDATE users SET referral_reward_days=0 WHERE id=?",
                    (referrer.id,),
                )
                rewarded = True
        db.commit()
        if rewarded and bot and referrer.telegram_id:
            try:
                await bot.send_message(
                    chat_id=referrer.telegram_id,
                    text=(
                        "🎉 <b>3 friends joined with your link!</b>\n\n"
                        f"👑 <b>{reward_days} day(s) of Premium added!</b> (until {until[:10]})\n\n"
                        "That was your one-time referral bonus! 💪\n"
                        "Keep sharing — every 3 friends still = +10 bonus swipes!"
                    ),
                    parse_mode="HTML",
                )
            except Exception as e:
                print(f"[BOT] referral reward notify failed: {e}")
    except Exception as e:
        print(f"[BOT] referral failed: {e}")
    finally:
        try:
            db.close()
        except Exception:
            pass
