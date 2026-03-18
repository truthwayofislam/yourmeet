import os
import asyncio
import libsql_experimental as libsql
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, Application
from telegram.request import HTTPXRequest

ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN", "")
ADMIN_TG_ID = os.getenv("ADMIN_TG_ID", "")  # Your personal Telegram ID
TURSO_URL = os.getenv("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.getenv("TURSO_DATABASE_KEY", "")

def get_conn():
    if TURSO_URL and TURSO_TOKEN:
        return libsql.connect(TURSO_URL, auth_token=TURSO_TOKEN)
    return libsql.connect("yourmeet.db")

def _verify_keyboard(user_id: int):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve:{user_id}"),
        InlineKeyboardButton("🚫 Block", callback_data=f"block:{user_id}"),
    ]])

async def send_for_review(bot, user_id: int, name: str, age: int, gender: str, city: str, photo: str):
    """Called after new user registers — sends profile to admin for review."""
    if not ADMIN_TG_ID:
        return
    caption = (
        f"🆕 *New Profile*\n\n"
        f"👤 *{name}*, {age} • {gender.capitalize()}\n"
        f"📍 {city or 'No city'}\n"
        f"🆔 DB ID: `{user_id}`"
    )
    try:
        if photo and photo.startswith("https://"):
            await bot.send_photo(
                chat_id=ADMIN_TG_ID,
                photo=photo,
                caption=caption,
                parse_mode="Markdown",
                reply_markup=_verify_keyboard(user_id)
            )
        else:
            await bot.send_message(
                chat_id=ADMIN_TG_ID,
                text=caption + "\n\n⚠️ No photo",
                parse_mode="Markdown",
                reply_markup=_verify_keyboard(user_id)
            )
    except Exception as e:
        print(f"[ADMIN BOT] Failed to send review: {e}")

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_TG_ID:
        await update.message.reply_text("❌ Unauthorized.")
        return
    await update.message.reply_text(
        "👮 *YourMeet Admin Bot*\n\n"
        "New user registrations will appear here.\n"
        "Tap ✅ Approve or 🚫 Block on each profile.\n\n"
        "/pending — Show unreviewed profiles\n"
        "/stats — App stats",
        parse_mode="Markdown"
    )

# /pending — show profiles not yet reviewed
async def pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_TG_ID:
        return
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name, age, gender, city, photo FROM users WHERE is_blocked=0 AND is_admin=0 ORDER BY id DESC LIMIT 20"
    ).fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("✅ No pending profiles.")
        return
    await update.message.reply_text(f"📋 Showing last {len(rows)} profiles:")
    for r in rows:
        uid, name, age, gender, city, photo = r
        caption = f"👤 *{name}*, {age} • {gender or '?'}\n📍 {city or 'No city'}\n🆔 ID: `{uid}`"
        try:
            if photo and photo.startswith("https://"):
                await update.message.reply_photo(photo, caption=caption, parse_mode="Markdown", reply_markup=_verify_keyboard(uid))
            else:
                await update.message.reply_text(caption + "\n⚠️ No photo", parse_mode="Markdown", reply_markup=_verify_keyboard(uid))
        except:
            await update.message.reply_text(caption + "\n⚠️ Photo load failed", parse_mode="Markdown", reply_markup=_verify_keyboard(uid))

# /stats
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_TG_ID:
        return
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin=0").fetchone()[0]
    blocked = conn.execute("SELECT COUNT(*) FROM users WHERE is_blocked=1").fetchone()[0]
    premium = conn.execute("SELECT COUNT(*) FROM users WHERE is_premium=1").fetchone()[0]
    matches = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    today_users = conn.execute("SELECT COUNT(*) FROM users WHERE date(created_at)=date('now')").fetchone()[0]
    conn.close()
    await update.message.reply_text(
        f"📊 *App Stats*\n\n"
        f"👥 Total Users: *{total}*\n"
        f"🆕 Joined Today: *{today_users}*\n"
        f"👑 Premium: *{premium}*\n"
        f"🚫 Blocked: *{blocked}*\n"
        f"💕 Total Matches: *{matches}*",
        parse_mode="Markdown"
    )

# Approve / Block callback
async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if str(query.from_user.id) != ADMIN_TG_ID:
        return
    action, uid = query.data.split(":")
    uid = int(uid)
    conn = get_conn()
    if action == "approve":
        conn.execute("UPDATE users SET is_blocked=0 WHERE id=?", (uid,))
        conn.commit()
        conn.close()
        await query.edit_message_caption(
            caption=query.message.caption + "\n\n✅ *Approved*",
            parse_mode="Markdown"
        )
    elif action == "block":
        conn.execute("UPDATE users SET is_blocked=1 WHERE id=?", (uid,))
        conn.commit()
        conn.close()
        try:
            await query.edit_message_caption(
                caption=query.message.caption + "\n\n🚫 *Blocked*",
                parse_mode="Markdown"
            )
        except:
            await query.edit_message_text(query.message.text + "\n\n🚫 *Blocked*", parse_mode="Markdown")

def build_admin_app() -> Application:
    request = HTTPXRequest(connect_timeout=20, read_timeout=20, write_timeout=20)
    app = ApplicationBuilder().token(ADMIN_BOT_TOKEN).request(request).updater(None).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pending", pending_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(verify_callback, pattern="^(approve|block):"))
    return app
