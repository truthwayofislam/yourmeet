import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, PreCheckoutQueryHandler, filters, ContextTypes,
)
from strings import get as s

BOT_TOKEN = os.getenv("TELEGRAM_BOTS_KEY", "")
APP_URL = os.getenv("APP_URL", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")


def build_bot() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("matches", cmd_matches))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("premium", cmd_premium))
    app.add_handler(CommandHandler("share", cmd_share))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("confirmdelete", cmd_confirm_delete))
    app.add_handler(CommandHandler("language", cmd_language))
    app.add_handler(CommandHandler("boost", cmd_boost))
    app.add_handler(CallbackQueryHandler(cb_language, pattern=r"^lang:"))
    app.add_handler(CallbackQueryHandler(cb_buy, pattern=r"^buy:"))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app


def _lang(update: Update) -> str:
    return (update.effective_user.language_code or "en")[:2]


def _get_user(tg_id: str):
    from database import get_conn, row_to_user, USER_COLS
    db = get_conn()
    cols = ", ".join(USER_COLS)
    row = db.execute(f"SELECT {cols} FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
    return row_to_user(row)


def _open_app_keyboard(lang: str):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(s(lang, "open_app"), web_app=WebAppInfo(url=APP_URL))
    ]])


# ── Commands ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    lang = _lang(update)
    tg_id = str(tg_user.id)

    # Handle referral
    if ctx.args:
        ref_code = ctx.args[0]
        if ref_code.startswith("ref_"):
            referrer_id = ref_code[4:]
            _handle_referral(tg_id, referrer_id)

    user = _get_user(tg_id)
    if user:
        lang = user.language or lang

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(s(lang, "open_app"), web_app=WebAppInfo(url=APP_URL))],
    ])
    await update.message.reply_text(s(lang, "welcome"), parse_mode="Markdown", reply_markup=keyboard)


async def cmd_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    lang = (user.language if user else _lang(update)) or "en"
    if not user or not user.photo:
        await update.message.reply_text(s(lang, "no_profile"), parse_mode="Markdown",
                                        reply_markup=_open_app_keyboard(lang))
        return
    premium = "✅" if user.is_premium else "❌"
    status = "Approved ✅" if user.is_approved else ("Rejected ❌" if user.is_rejected else "Pending ⏳")
    text = s(lang, "your_profile", name=user.name, age=user.age,
             gender=user.gender or "-", city=user.city or "-",
             premium=premium, status=status)
    await update.message.reply_text(text, parse_mode="Markdown",
                                    reply_markup=_open_app_keyboard(lang))


async def cmd_matches(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    lang = (user.language if user else _lang(update)) or "en"
    if not user:
        await update.message.reply_text(s(lang, "no_profile"), parse_mode="Markdown")
        return
    from database import get_conn
    db = get_conn()
    count = db.execute(
        "SELECT COUNT(*) FROM matches WHERE user1_id=? OR user2_id=?",
        (user.id, user.id)
    ).fetchone()[0]
    await update.message.reply_text(s(lang, "your_matches", count=count), parse_mode="Markdown",
                                    reply_markup=_open_app_keyboard(lang))


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    lang = (user.language if user else _lang(update)) or "en"
    if not user:
        await update.message.reply_text(s(lang, "no_profile"), parse_mode="Markdown")
        return
    from database import get_conn
    db = get_conn()
    given = db.execute("SELECT COUNT(*) FROM likes WHERE from_user=?", (user.id,)).fetchone()[0]
    received = db.execute("SELECT COUNT(*) FROM likes WHERE to_user=?", (user.id,)).fetchone()[0]
    matches = db.execute(
        "SELECT COUNT(*) FROM matches WHERE user1_id=? OR user2_id=?", (user.id, user.id)
    ).fetchone()[0]
    await update.message.reply_text(
        s(lang, "your_stats", given=given, received=received,
          matches=matches, swipes=user.daily_swipes),
        parse_mode="Markdown"
    )


async def cmd_premium(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    lang = (user.language if user else _lang(update)) or "en"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("1 Month — 150 ⭐", callback_data="buy:monthly")],
        [InlineKeyboardButton("3 Months — 350 ⭐", callback_data="buy:quarterly")],
        [InlineKeyboardButton(s(lang, "open_app"), web_app=WebAppInfo(url=f"{APP_URL}/premium"))],
    ])
    await update.message.reply_text(s(lang, "premium_info"), parse_mode="Markdown",
                                    reply_markup=keyboard)


async def cmd_share(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    lang = (user.language if user else _lang(update)) or "en"
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{tg_id}"
    await update.message.reply_text(s(lang, "referral_msg", link=link), parse_mode="Markdown")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang = _lang(update)
    user = _get_user(str(update.effective_user.id))
    if user:
        lang = user.language or lang
    await update.message.reply_text(s(lang, "help_msg"), parse_mode="Markdown")


async def cmd_about(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang = _lang(update)
    user = _get_user(str(update.effective_user.id))
    if user:
        lang = user.language or lang
    await update.message.reply_text(s(lang, "about_msg"), parse_mode="Markdown")


async def cmd_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    lang = (user.language if user else _lang(update)) or "en"
    await update.message.reply_text(s(lang, "delete_confirm"), parse_mode="Markdown")


async def cmd_confirm_delete(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    lang = (user.language if user else _lang(update)) or "en"
    if user:
        from database import get_conn
        db = get_conn()
        from routers.auth import _delete_user_data
        _delete_user_data(db, user.id)
    await update.message.reply_text(s(lang, "account_deleted"), parse_mode="Markdown")


async def cmd_language(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    langs = [
        ("🇬🇧 English", "en"), ("🇪🇸 Español", "es"), ("🇷🇺 Русский", "ru"),
        ("🇰🇷 한국어", "ko"), ("🇨🇳 中文", "zh"), ("🇮🇩 Indonesia", "id"),
        ("🇸🇦 العربية", "ar"), ("🇧🇷 Português", "pt"), ("🇫🇷 Français", "fr"),
        ("🇩🇪 Deutsch", "de"), ("🇹🇷 Türkçe", "tr"), ("🇮🇹 Italiano", "it"),
        ("🇯🇵 日本語", "ja"), ("🇮🇳 हिंदी", "hi"),
    ]
    buttons = [[InlineKeyboardButton(name, callback_data=f"lang:{code}")] for name, code in langs]
    await update.message.reply_text(
        "🌐 Choose your language / Выберите язык / 언어 선택:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def cmd_boost(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    lang = (user.language if user else _lang(update)) or "en"
    if not user or not user.is_premium:
        await update.message.reply_text(s(lang, "btn_upgrade"), parse_mode="Markdown",
                                        reply_markup=_open_app_keyboard(lang))
        return
    from datetime import datetime, timedelta
    from database import get_conn
    db = get_conn()
    until = (datetime.utcnow() + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE users SET boosted_until=? WHERE id=?", (until, user.id))
    db.commit()
    await update.message.reply_text(s(lang, "boost_active"), parse_mode="Markdown")


# ── Callbacks ─────────────────────────────────────────────────────────────────

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


async def cb_language(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = query.data.split(":")[1]
    tg_id = str(update.effective_user.id)
    user = _get_user(tg_id)
    if user:
        from database import get_conn
        db = get_conn()
        db.execute("UPDATE users SET language=? WHERE telegram_id=?", (lang, tg_id))
        db.commit()
    await query.edit_message_text(s(lang, "language_changed"), parse_mode="Markdown")


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
    user = _get_user(tg_id)
    lang = (user.language if user else "en") or "en"
    premium_until = user.premium_until[:10] if user and user.premium_until else "-"
    await update.message.reply_text(
        s(lang, "premium_activated", date=premium_until), parse_mode="Markdown"
    )


# ── Message forwarding (chat sessions) ───────────────────────────────────────

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    text = update.message.text or ""
    from database import get_conn
    db = get_conn()
    from routers.chat import forward_message
    forwarded = await forward_message(tg_id, text, db)
    if not forwarded:
        user = _get_user(tg_id)
        lang = (user.language if user else "en") or "en"
        keyboard = _open_app_keyboard(lang)
        await update.message.reply_text(
            s(lang, "open_app"), reply_markup=keyboard
        )


# ── Notify helpers ────────────────────────────────────────────────────────────

async def notify_match(bot, user, matched_with):
    """Notify user about a new match."""
    if not user.telegram_id:
        return
    lang = user.language or "en"
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(s(lang, "open_app"), web_app=WebAppInfo(url=f"{APP_URL}/matches"))
    ]])
    try:
        await bot.send_message(
            chat_id=user.telegram_id,
            text=s(lang, "match_notify", name=matched_with.name),
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
    except Exception as e:
        print(f"[BOT] notify_match failed: {e}")


# ── Referral helper ───────────────────────────────────────────────────────────

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
        count = db.execute(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (referrer.id,)
        ).fetchone()[0] + 1
        db.execute("UPDATE users SET referral_count=? WHERE id=?", (count, referrer.id))
        if count % 3 == 0:
            db.execute("UPDATE users SET daily_swipes=daily_swipes+10 WHERE id=?", (referrer.id,))
        db.commit()
    except Exception as e:
        print(f"[BOT] referral failed: {e}")
