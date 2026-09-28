import os
import json
import warnings
warnings.filterwarnings("ignore", message=".*per_message=False.*", category=UserWarning)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes,
)
from strings import get as s

BOT_TOKEN = os.getenv("TELEGRAM_BOTS_KEY", "")
APP_URL = os.getenv("APP_URL", "")

# Conversation states
(
    SETUP_NAME, SETUP_AGE, SETUP_GENDER, SETUP_INTERESTED_IN,
    SETUP_CITY, SETUP_BIO, SETUP_SOCIAL, SETUP_PHOTO,
) = range(8)


def build_bot() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    setup_conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CallbackQueryHandler(cb_setup_start, pattern=r"^setup:start$"),
        ],
        states={
            SETUP_NAME:          [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_name)],
            SETUP_AGE:           [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_age)],
            SETUP_GENDER:        [CallbackQueryHandler(setup_gender, pattern=r"^sg:")],
            SETUP_INTERESTED_IN: [CallbackQueryHandler(setup_interested_in, pattern=r"^si:")],
            SETUP_CITY:          [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_city)],
            SETUP_BIO:           [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_bio)],
            SETUP_SOCIAL:        [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_social)],
            SETUP_PHOTO:         [MessageHandler(filters.PHOTO, setup_photo)],
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
    app.add_handler(CommandHandler("block", cmd_block))
    app.add_handler(CommandHandler("filters", cmd_filters))
    app.add_handler(CommandHandler("editprofile", cmd_edit_profile))
    app.add_handler(CallbackQueryHandler(cb_language, pattern=r"^lang:"))
    app.add_handler(CallbackQueryHandler(cb_buy, pattern=r"^buy:"))
    app.add_handler(CallbackQueryHandler(cb_vibe, pattern=r"^vibe:"))
    app.add_handler(CallbackQueryHandler(cb_like, pattern=r"^like:"))
    app.add_handler(CallbackQueryHandler(cb_skip, pattern=r"^skip:"))
    app.add_handler(CallbackQueryHandler(cb_superlike, pattern=r"^superlike:"))
    app.add_handler(CallbackQueryHandler(cb_next, pattern=r"^next$"))
    app.add_handler(CallbackQueryHandler(cb_unmatch, pattern=r"^unmatch:"))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(CallbackQueryHandler(cb_terms_accept, pattern=r"^terms:accept$"))
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


def _browse_keyboard(target_id: int, super_left: int):
    sl_text = f"⭐ Super Like ({super_left})" if super_left > 0 else "⭐ Super Like (0)"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("❤️ Like", callback_data=f"like:{target_id}"),
            InlineKeyboardButton("👎 Skip", callback_data=f"skip:{target_id}"),
        ],
        [InlineKeyboardButton(sl_text, callback_data=f"superlike:{target_id}")],
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
        _save_msg_id(tg_id, m.message_id)
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
    _save_msg_id(tg_id, query.message.message_id)


async def cb_setup_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(update.effective_user.id)
    await query.edit_message_text(
        "Let's set up your profile! 🎉\n\nWhat's your <b>name</b>?",
        parse_mode="HTML"
    )
    _save_msg_id(tg_id, query.message.message_id)


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    sent = await update.message.reply_text("Setup cancelled. Use /start to begin again.")
    await _track_bot_message(ctx, sent.message_id)
    return ConversationHandler.END


async def cmd_about(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _cleanup_chat(ctx, update.effective_chat.id)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Browse Profiles", callback_data="next")],
    ])
    sent = await update.message.reply_text(
        "ℹ️ <b>About YourMeet</b>\n\n"
        "YourMeet is a dating app where you swipe, match, and chat — all through Telegram.\n\n"
        "Developer: @who_is_the-black_hat\n"
        "Stack: FastAPI + Python Telegram Bot + Turso\n\n"
        "Use /premium to unlock unlimited swipes, super likes, and chat time! 👑",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    ctx.user_data["last_keyboard_msg_id"] = sent.message_id
    await _track_bot_message(ctx, sent.message_id)


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
    s = await _get_setup_data(tg_id)
    d = s["data"]
    d["name"] = name
    _save_setup_data(tg_id, data=d)
    await _edit_setup_msg(ctx, chat_id, tg_id, f"Nice, <b>{name}</b>! How old are you?", parse_mode="HTML")
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
        await _edit_setup_msg(ctx, chat_id, tg_id, "Please enter a valid city name:")
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
        await _edit_setup_msg(ctx, chat_id, tg_id, "Bio too short (min 10 chars). Try again:")
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
    await _edit_setup_msg(
        ctx, chat_id, tg_id,
        "Almost done! Send your best <b>profile photo</b>\n\n"
        "<i>Make sure your face is clearly visible.</i>",
        parse_mode="HTML"
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

    s = await _get_setup_data(tg_id)
    d = s["data"]
    name = d.get("name", update.effective_user.first_name or "User")
    age = d.get("age")
    gender = d.get("gender")
    interested_in = d.get("interested_in", "both")
    city = d.get("city", "")
    bio = d.get("bio", "")
    social = d.get("social_handle", "")

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
    # doesn't force them back into the review queue.
    edit_mode = bool(d.get("_edit_mode"))

    if existing:
        if edit_mode:
            db.execute(
                """UPDATE users SET name=?, age=?, gender=?, interested_in=?, bio=?, city=?,
                   lat=?, lng=?, social_handle=?, photo=?, photos=?,
                   terms_accepted=1 WHERE id=?""",
                (name, age, gender, interested_in, bio, city,
                 lat, lng, social, file_id, photos_json, existing.id),
            )
        else:
            db.execute(
                """UPDATE users SET name=?, age=?, gender=?, interested_in=?, bio=?, city=?,
                   lat=?, lng=?, social_handle=?, photo=?, photos=?,
                   is_approved=0, is_rejected=0, terms_accepted=1 WHERE id=?""",
                (name, age, gender, interested_in, bio, city,
                 lat, lng, social, file_id, photos_json, existing.id),
            )
        user_id = existing.id
    else:
        db.execute(
            """INSERT INTO users
               (name, age, gender, interested_in, bio, city, lat, lng,
                social_handle, photo, photos, telegram_id, is_approved, terms_accepted)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,1)""",
            (name, age, gender, interested_in, bio, city,
             lat, lng, social, file_id, photos_json, tg_id),
        )
        row = db.execute("SELECT id FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
        user_id = row[0]

    db.commit()
    db.close()

    ref_id = d.get("pending_ref") or ctx.user_data.get("pending_ref")
    if ref_id:
        _handle_referral(tg_id, ref_id)

    try:
        from admin_bot import send_for_review
        if not edit_mode:
            await send_for_review(user_id, name, age, gender, city, file_id)
    except Exception as e:
        print(f"[SETUP] admin notify failed: {e}")

    if edit_mode:
        sent = await ctx.bot.send_message(
            chat_id,
            f"✅ <b>Profile updated!</b>\n\nYour changes are live. Approval status preserved.",
            parse_mode="HTML"
        )
    else:
        sent = await ctx.bot.send_message(
            chat_id,
            "<b>Profile submitted!</b>\n\n"
            "Our team will review your profile within a few hours.\n"
            "You will get a notification once approved!\n\n"
            "Use /help to see all commands.",
            parse_mode="HTML"
        )
    await _track_bot_message(ctx, sent.message_id)
    return ConversationHandler.END


# ── Browse / Swipe ────────────────────────────────────────────────────────────

async def cmd_browse(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
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

    cols = ", ".join(USER_COLS)
    liked = [r[0] for r in db.execute("SELECT to_user FROM likes WHERE from_user=?", (user.id,)).fetchall()]
    skipped = [r[0] for r in db.execute("SELECT skipped_id FROM skips WHERE user_id=?", (user.id,)).fetchall()]
    excluded = list(set(liked + skipped + [user.id]))
    placeholders = ",".join("?" * len(excluded))

    interested_in = getattr(user, "interested_in", "both") or "both"
    if interested_in == "both":
        gender_sql = "gender IN ('male','female')"
        gender_params = ()
    else:
        gender_sql = "gender=?"
        gender_params = (interested_in,)

    row = db.execute(
        f"""SELECT {cols} FROM users
            WHERE id NOT IN ({placeholders})
            AND {gender_sql} AND age >= 18
            AND is_blocked=0 AND is_rejected=0 AND is_approved=1
            ORDER BY CASE WHEN boosted_until > datetime('now') THEN 0 ELSE 1 END, RANDOM()
            LIMIT 1""",
        (*excluded, *gender_params),
    ).fetchone()
    db.close()

    if not row:
        sent = await message.reply_text(
            "😔 No more profiles right now!\n\nCheck back later or invite friends with /share"
        )
        if ctx:
            ctx.user_data["last_keyboard_msg_id"] = sent.message_id
            await _track_bot_message(ctx, sent.message_id)
        return

    profile = row_to_user(row)
    super_left = getattr(user, "super_likes_left", 1)

    try:
        interests = json.loads(profile.interests or "[]")
    except Exception:
        interests = []

    caption = (
        f"<b>{profile.name}, {profile.age}</b> 📍 {profile.city or '-'}\n\n"
        f"{profile.bio or ''}\n"
    )
    if interests:
        caption += f"\n🏷 {' · '.join(interests[:5])}"
    if profile.is_verified:
        caption += "\n✅ Verified"

    keyboard = _browse_keyboard(profile.id, super_left)

    try:
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
    if matched:
        from database import get_conn, row_to_user, USER_COLS
        db = get_conn()
        cols = ", ".join(USER_COLS)
        target = row_to_user(db.execute(f"SELECT {cols} FROM users WHERE id=?", (target_id,)).fetchone())
        db.close()
        if target:
            sent = await query.message.reply_text(
                f"🎉 <b>It's a Match!</b>\n\nYou and <b>{target.name}</b> liked each other! 💕\n\n"
                f"Start chatting — just send a message here!",
                parse_mode="HTML"
            )
            await _track_bot_message(ctx, sent.message_id)
    await _send_next_profile(query.message, _get_user(tg_id), ctx)


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
    if matched:
        from database import get_conn, row_to_user, USER_COLS
        db = get_conn()
        cols = ", ".join(USER_COLS)
        target = row_to_user(db.execute(f"SELECT {cols} FROM users WHERE id=?", (target_id,)).fetchone())
        db.close()
        if target:
            sent = await query.message.reply_text(
                f"🎉 <b>It's a Match!</b>\n\nYou and <b>{target.name}</b> liked each other! 💕",
                parse_mode="HTML"
            )
            await _track_bot_message(ctx, sent.message_id)
    await _send_next_profile(query.message, _get_user(tg_id), ctx)


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
    await _send_next_profile(query.message, user, ctx)


async def cb_next(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    if not user:
        return
    await _send_next_profile(query.message, user, ctx)


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
        if not already:
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

        mutual = db.execute(
            "SELECT id FROM likes WHERE from_user=? AND to_user=?", (target_id, user.id)
        ).fetchone()

        match_id = None
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
                except DuplicateError:
                    # Concurrent request created the match — fetch it.
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
    return False


# ── Commands ──────────────────────────────────────────────────────────────────

async def cmd_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    lang = (user.language if user else _lang(update)) or "en"
    if not user or not user.photo:
        await _cleanup_chat(ctx, update.effective_chat.id)
        sent = await update.message.reply_text("No profile yet. Use /start to create one.")
        await _track_bot_message(ctx, sent.message_id)
        return
    premium = "✅" if user.is_premium else "❌"
    status = "Approved ✅" if user.is_approved else ("Rejected ❌" if user.is_rejected else "Pending ⏳")
    text = s(lang, "your_profile", name=user.name, age=user.age,
             gender=user.gender or "-", city=user.city or "-",
             premium=premium, status=status)
    await _cleanup_chat(ctx, update.effective_chat.id)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Browse Profiles", callback_data="next")],
    ])
    sent = await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    ctx.user_data["last_keyboard_msg_id"] = sent.message_id
    await _track_bot_message(ctx, sent.message_id)


async def cmd_matches(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    lang = (user.language if user else _lang(update)) or "en"
    if not user:
        await _cleanup_chat(ctx, update.effective_chat.id)
        sent = await update.message.reply_text("No profile yet. Use /start to create one.")
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
        await _cleanup_chat(ctx, update.effective_chat.id)
        sent = await update.message.reply_text("💔 No matches yet. Use /browse to find people!")
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
            text += f"• <b>{other.name}</b>, {other.age} — {other.city or '-'}\n"
            buttons.append([InlineKeyboardButton(
                f"💬 Chat with {other.name}", callback_data=f"unmatch:{match_id}"
            )])
    db2.close()
    await _cleanup_chat(ctx, update.effective_chat.id)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Browse More", callback_data="next")],
    ])
    sent = await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    ctx.user_data["last_keyboard_msg_id"] = sent.message_id
    await _track_bot_message(ctx, sent.message_id)


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
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
    await _cleanup_chat(ctx, update.effective_chat.id)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Browse Profiles", callback_data="next")],
    ])
    sent = await update.message.reply_text(
        s(lang, "your_stats", given=given, received=received,
          matches=matches, swipes=user.daily_swipes),
        parse_mode="HTML",
        reply_markup=keyboard
    )
    ctx.user_data["last_keyboard_msg_id"] = sent.message_id
    await _track_bot_message(ctx, sent.message_id)


async def cmd_premium(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
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
        [InlineKeyboardButton("🔍 Browse Profiles", callback_data="next")],
    ])
    sent = await update.message.reply_text(s(lang, "referral_msg", link=link), reply_markup=keyboard)
    ctx.user_data["last_keyboard_msg_id"] = sent.message_id
    await _track_bot_message(ctx, sent.message_id)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _cleanup_chat(ctx, update.effective_chat.id)
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👤 Profile", callback_data="cmd:profile"),
            InlineKeyboardButton("🔍 Browse", callback_data="cmd:browse"),
            InlineKeyboardButton("💕 Matches", callback_data="cmd:matches"),
            InlineKeyboardButton("📊 Stats", callback_data="cmd:stats"),
        ],
        [
            InlineKeyboardButton("👑 Premium", callback_data="cmd:premium"),
            InlineKeyboardButton("🔗 Share", callback_data="cmd:share"),
            InlineKeyboardButton("🌐 Language", callback_data="cmd:language"),
            InlineKeyboardButton("🚀 Boost", callback_data="cmd:boost"),
        ],
        [
            InlineKeyboardButton("🚫 Block", callback_data="cmd:block"),
            InlineKeyboardButton("🎯 Filters", callback_data="cmd:filters"),
            InlineKeyboardButton("✏️ Edit", callback_data="cmd:editprofile"),
            InlineKeyboardButton("🗑 Delete", callback_data="cmd:delete"),
        ],
        [InlineKeyboardButton("❓ About", callback_data="cmd:about")],
    ])
    sent = await update.message.reply_text(
        "📖 <b>YourMeet Commands</b>\n\nTap a button below to run it.",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    ctx.user_data["last_keyboard_msg_id"] = sent.message_id
    await _track_bot_message(ctx, sent.message_id)


async def cmd_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _cleanup_chat(ctx, update.effective_chat.id)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Browse Profiles", callback_data="next")],
    ])
    sent = await update.message.reply_text(
        "⚠️ Are you sure you want to delete your account?\n\n"
        "This will permanently delete all your data, matches and messages.\n\n"
        "Type /confirmdelete to confirm.",
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
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Browse Profiles", callback_data="next")],
    ])
    sent = await update.message.reply_text(s(lang, "boost_active"), parse_mode="HTML", reply_markup=keyboard)
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
        await update.message.reply_text("Usage: /block <user_id>")
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
        await update.message.reply_text(
            f"🎯 <b>Your Filters</b>\n\n"
            f"Min age: {ma or 'any'}\n"
            f"Max age: {xa or 'any'}\n"
            f"Max distance: {md or 'any'} km\n\n"
            f"Usage: /filters <min> <max> <km>\nExample: /filters 20 35 50",
            parse_mode="HTML"
        )
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
    _save_msg_id(tg_id, m.message_id)
    await _track_bot_message(ctx, m.message_id)
    return SETUP_NAME


# ── Callbacks ─────────────────────────────────────────────────────────────────

async def cb_unmatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    match_id = int(query.data.split(":")[1])
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    if not user:
        return
    from database import get_conn
    db = get_conn()
    db.execute(
        "DELETE FROM matches WHERE id=? AND (user1_id=? OR user2_id=?)",
        (match_id, user.id, user.id)
    )
    db.commit()
    db.close()
    await query.edit_message_text("✅ Unmatched.")


async def cb_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Dispatch /help buttons (cmd:<name>) to the matching command."""
    query = update.callback_query
    await query.answer()
    cmd = query.data.split(":")[1] if ":" in query.data else ""
    if not cmd:
        return
    handlers = {
        "profile": cmd_profile,
        "browse": cmd_browse,
        "matches": cmd_matches,
        "stats": cmd_stats,
        "premium": cmd_premium,
        "share": cmd_share,
        "language": cmd_language,
        "boost": cmd_boost,
        "block": cmd_block,
        "filters": cmd_filters,
        "editprofile": cmd_edit_profile,
        "delete": cmd_delete,
        "about": cmd_about,
        "help": cmd_help,
    }
    handler = handlers.get(cmd)
    if not handler:
        return
    # Build a fake message update so the command runs as if typed
    fake_update = Update.de_json(
        {"update_id": update.update_id, "message": {
            "message_id": query.message.message_id,
            "date": int(__import__("time").time()),
            "chat": {"id": query.message.chat_id, "type": "private"},
            "from": {"id": update.effective_user.id, "first_name": update.effective_user.first_name or "User"},
            "text": f"/{cmd}",
        }},
        ctx.bot,
    )
    await handler(fake_update, ctx)


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
    result = await submit_vibe_answer(match_id, {"answer": answer}, db=get_conn(), current_user=user)
    await query.edit_message_reply_markup(reply_markup=None)
    try:
        import json as _json
        body = _json.loads(result.body) if hasattr(result, "body") else {}
        if body.get("waiting"):
            sent = await query.message.reply_text("✅ Answer recorded! Waiting for your match...")
            await _track_bot_message(ctx, sent.message_id)
        elif "matched_vibe" in body:
            if body["matched_vibe"]:
                sent = await query.message.reply_text(
                    f"✨ Vibe Match! You both chose: <b>{body.get('my_choice', '')}</b> 💕",
                    parse_mode="HTML"
                )
                await _track_bot_message(ctx, sent.message_id)
            else:
                sent = await query.message.reply_text(
                    f"🎭 You: <b>{body.get('my_choice', '')}</b> | Match: <b>{body.get('other_choice', '')}</b>",
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
    await update.pre_checkout_query.answer(ok=True)


async def successful_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    payload = update.message.successful_payment.invoice_payload
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
    text = update.message.text or ""
    from database import get_conn
    db = get_conn()
    from routers.chat import forward_message
    forwarded = await forward_message(tg_id, text, db)
    db.close()
    if not forwarded:
        await _cleanup_chat(ctx, update.effective_chat.id)
        sent = await update.message.reply_text(
            "💬 No active chat.\n\nUse /matches to see your matches and start chatting!"
        )
        await _track_bot_message(ctx, sent.message_id)


# ── Notify helpers ────────────────────────────────────────────────────────────

async def notify_match(bot, user, matched_with):
    if not user.telegram_id:
        return
    lang = user.language or "en"
    try:
        await bot.send_message(
            chat_id=user.telegram_id,
            text=s(lang, "match_notify", name=matched_with.name),
            parse_mode="HTML",
        )
    except Exception as e:
        print(f"[BOT] notify_match failed: {e}")


# ── Referral ──────────────────────────────────────────────────────────────────

def _handle_referral(new_tg_id: str, referrer_tg_id: str):
    if new_tg_id == referrer_tg_id:
        return
    try:
        from database import get_conn
        db = get_conn()
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
        if count % 3 == 0:
            db.execute("UPDATE users SET daily_swipes=daily_swipes+10 WHERE id=?", (referrer.id,))
        db.commit()
        db.close()
    except Exception as e:
        print(f"[BOT] referral failed: {e}")
