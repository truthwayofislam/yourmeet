import os
import httpx
import libsql_experimental as libsql
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, Application
from telegram.request import HTTPXRequest

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

async def send_for_review(user_id: int, name: str, age: int, gender: str, city: str, photo: str, email: str = "", phone: str = ""):
    """Send new profile to admin via direct HTTP — no bot instance needed."""
    token = os.getenv("ADMIN_BOT_TOKEN", "").strip()
    admin_tg_id = os.getenv("ADMIN_TG_ID", "").strip()
    print(f"[ADMIN BOT] send_for_review called — token={'SET' if token else 'MISSING'}, admin_id={admin_tg_id or 'MISSING'}")
    if not token or not admin_tg_id:
        return
    api = f"https://api.telegram.org/bot{token}"
    caption = (
        f"🆕 *New Profile*\n\n"
        f"👤 *{name}*, {age} • {gender.capitalize() if gender else '?'}\n"
        f"📍 {city or 'No city'}\n"
        f"📧 {email or 'N/A'}\n"
        f"📞 {phone or 'N/A'}\n"
        f"🆔 DB ID: `{user_id}`"
    )
    keyboard = {"inline_keyboard": [[
        {"text": "✅ Approve", "callback_data": f"approve:{user_id}"},
        {"text": "🚫 Block", "callback_data": f"block:{user_id}"}
    ]]}
    try:
        import json
        async with httpx.AsyncClient(timeout=30) as client:
            if photo and photo.startswith("https://"):
                # Download photo bytes first (Telegram can't fetch its own URLs)
                photo_resp = await client.get(photo)
                if photo_resp.status_code == 200:
                    resp = await client.post(f"{api}/sendPhoto", data={
                        "chat_id": admin_tg_id,
                        "caption": caption,
                        "parse_mode": "Markdown",
                        "reply_markup": json.dumps(keyboard)
                    }, files={"photo": ("photo.jpg", photo_resp.content, "image/jpeg")})
                else:
                    resp = await client.post(f"{api}/sendMessage", json={
                        "chat_id": admin_tg_id,
                        "text": caption + "\n\n⚠️ Photo download failed",
                        "parse_mode": "Markdown",
                        "reply_markup": keyboard
                    })
            else:
                resp = await client.post(f"{api}/sendMessage", json={
                    "chat_id": admin_tg_id,
                    "text": caption + "\n\n⚠️ No photo",
                    "parse_mode": "Markdown",
                    "reply_markup": keyboard
                })
            print(f"[ADMIN BOT] Review sent: {resp.status_code} - {resp.text[:150]}")
    except Exception as e:
        print(f"[ADMIN BOT] Failed: {e}")

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != os.getenv("ADMIN_TG_ID", "").strip():
        await update.message.reply_text("❌ Unauthorized.")
        return
    await update.message.reply_text(
        "👮 *YourMeet Admin Bot*\n\n"
        "New user registrations will appear here.\n"
        "Tap ✅ Approve or 🚫 Block on each profile.\n\n"
        "/pending — Show recent profiles\n"
        "/stats — App stats",
        parse_mode="Markdown"
    )

# /pending
async def pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != os.getenv("ADMIN_TG_ID", "").strip():
        return
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name, age, gender, city, photo FROM users WHERE is_admin=0 ORDER BY id DESC LIMIT 20"
    ).fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("No profiles found.")
        return
    await update.message.reply_text(f"📋 Last {len(rows)} profiles:")
    for r in rows:
        uid, name, age, gender, city, photo = r
        caption = f"👤 *{name}*, {age} • {gender or '?'}\n📍 {city or 'No city'}\n🆔 ID: `{uid}`"
        try:
            if photo and photo.startswith("https://"):
                import httpx as _httpx
                async with _httpx.AsyncClient(timeout=20) as cl:
                    pr = await cl.get(photo)
                if pr.status_code == 200:
                    await update.message.reply_photo(pr.content, caption=caption, parse_mode="Markdown", reply_markup=_verify_keyboard(uid))
                else:
                    await update.message.reply_text(caption + "\n⚠️ Photo error", parse_mode="Markdown", reply_markup=_verify_keyboard(uid))
            else:
                await update.message.reply_text(caption + "\n⚠️ No photo", parse_mode="Markdown", reply_markup=_verify_keyboard(uid))
        except:
            await update.message.reply_text(caption + "\n⚠️ Photo error", parse_mode="Markdown", reply_markup=_verify_keyboard(uid))

# /stats
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != os.getenv("ADMIN_TG_ID", "").strip():
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
    if str(query.from_user.id) != os.getenv("ADMIN_TG_ID", "").strip():
        return
    action, uid = query.data.split(":")
    uid = int(uid)
    conn = get_conn()
    conn.execute("UPDATE users SET is_blocked=? WHERE id=?", (0 if action == "approve" else 1, uid))
    conn.commit()
    conn.close()
    label = "✅ Approved" if action == "approve" else "🚫 Blocked"
    try:
        await query.edit_message_caption(
            caption=query.message.caption + f"\n\n{label}",
            parse_mode="Markdown"
        )
    except:
        try:
            await query.edit_message_text(query.message.text + f"\n\n{label}", parse_mode="Markdown")
        except: pass

def build_admin_app() -> Application:
    token = os.getenv("ADMIN_BOT_TOKEN", "").strip()
    request = HTTPXRequest(connect_timeout=20, read_timeout=20, write_timeout=20)
    app = ApplicationBuilder().token(token).request(request).updater(None).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pending", pending_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(verify_callback, pattern="^(approve|block):"))
    return app
