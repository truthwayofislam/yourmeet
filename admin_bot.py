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
        InlineKeyboardButton("✅⭐ Approve+Verify", callback_data=f"verify:{user_id}"),
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
        {"text": "✅⭐ Approve+Verify", "callback_data": f"verify:{user_id}"},
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
        "/remind — Message users with incomplete profiles\n"
        "/remind_blocked — Send rejection message to all blocked users\n"
        "/stats — App stats",
        parse_mode="Markdown"
    )

# /pending
async def pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != os.getenv("ADMIN_TG_ID", "").strip():
        return
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name, age, gender, city, photo FROM users WHERE is_admin=0 AND is_approved=0 AND is_blocked=0 ORDER BY id DESC LIMIT 20"
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

# /remind — message incomplete profile users
async def remind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != os.getenv("ADMIN_TG_ID", "").strip():
        return
    conn = get_conn()
    rows = conn.execute(
        "SELECT telegram_id, name, photo, bio, city, social_handle FROM users "
        "WHERE is_admin=0 AND is_blocked=0 AND telegram_id IS NOT NULL AND telegram_id != '' "
        "AND (photo='' OR bio='' OR city='' OR social_handle='')"
    ).fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("\u2705 All users have complete profiles!")
        return
    bot_token = os.getenv("TELEGRAM_BOTS_KEY", "").strip()
    app_url = os.getenv("APP_URL", "")
    api = f"https://api.telegram.org/bot{bot_token}"
    sent = 0
    import json
    async with httpx.AsyncClient(timeout=20) as client:
        for tg_id, name, photo, bio, city, social in rows:
            missing = []
            if not photo: missing.append("\U0001f4f8 Profile photo")
            if not bio: missing.append("\U0001f4ac Bio")
            if not city: missing.append("\U0001f4cd City")
            if not social: missing.append("\U0001f4f1 Instagram/Telegram handle")
            if not missing:
                continue
            text = (
                f"\U0001f44b Hey *{name or 'there'}*!\n\n"
                f"Your YourMeet profile is incomplete. Add these to get *real matches*:\n\n"
                + "\n".join(f"\u2022 {m}" for m in missing)
                + "\n\nComplete your profile now \U0001f447"
            )
            keyboard = {"inline_keyboard": [[{"text": "\u270f\ufe0f Complete Profile", "url": f"{app_url}/profile"}]]}
            try:
                await client.post(f"{api}/sendMessage", json={
                    "chat_id": tg_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "reply_markup": keyboard
                })
                sent += 1
            except: pass
    await update.message.reply_text(f"\u2705 Reminders sent to *{sent}* users.", parse_mode="Markdown")

async def _notify_user(tg_id: str, text: str, keyboard: dict):
    main_token = os.getenv("TELEGRAM_BOTS_KEY", "").strip()
    if not main_token or not tg_id:
        print(f"[NOTIFY] Skipped — token={'SET' if main_token else 'MISSING'}, tg_id={tg_id!r}")
        return
    try:
        async with httpx.AsyncClient(timeout=10) as cl:
            resp = await cl.post(f"https://api.telegram.org/bot{main_token}/sendMessage", json={
                "chat_id": tg_id, "text": text, "parse_mode": "Markdown", "reply_markup": keyboard
            })
            print(f"[NOTIFY] tg_id={tg_id} status={resp.status_code} body={resp.text[:200]}")
    except Exception as e:
        print(f"[NOTIFY] Exception: {e}")

# /remind_blocked — notify already blocked users
async def remind_blocked_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != os.getenv("ADMIN_TG_ID", "").strip():
        return
    conn = get_conn()
    rows = conn.execute(
        "SELECT telegram_id, name FROM users WHERE is_blocked=1 AND telegram_id IS NOT NULL AND telegram_id != ''"
    ).fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("No blocked users with Telegram ID.")
        return
    sent = 0
    for tg_id, name in rows:
        await _notify_user(tg_id,
            f"🚫 *{name}*, your profile was rejected.\n\n"
            f"Your profile was rejected. Please re-register with a clear photo and genuine bio.",
            {"inline_keyboard": [[{"text": "🔄 Re-register", "url": f"{os.getenv('APP_URL', '')}/register"}]]}
        )
        sent += 1
    await update.message.reply_text(f"✅ Rejection message sent to *{sent}* blocked users.", parse_mode="Markdown")

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
    if str(query.from_user.id) != os.getenv("ADMIN_TG_ID", "").strip():
        await query.answer()
        return
    action, uid = query.data.split(":")
    uid = int(uid)
    print(f"[ADMIN] verify_callback: action={action}, uid={uid}")
    conn = get_conn()
    if action == "approve":
        conn.execute("UPDATE users SET is_blocked=0, is_approved=1 WHERE id=?", (uid,))
        label = "✅ Approved"
    elif action == "verify":
        conn.execute("UPDATE users SET is_blocked=0, is_verified=1, is_approved=1 WHERE id=?", (uid,))
        label = "✅ Approved + Verified ⭐"
    else:
        conn.execute("UPDATE users SET is_blocked=1, is_approved=1 WHERE id=?", (uid,))
        label = "🚫 Blocked"
    conn.commit()
    tg_row = conn.execute("SELECT telegram_id, name FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    print(f"[ADMIN] tg_row for uid={uid}: {tg_row}")
    if not tg_row or not tg_row[0]:
        await query.answer(f"⚠️ {tg_row[1] if tg_row else 'User'} has no Telegram — message not sent", show_alert=True)
    else:
        await query.answer()
        tg_id, name = tg_row
        app_url = os.getenv("APP_URL", "")
        if action in ("approve", "verify"):
            verified_line = "\n⭐ Your profile is also *Verified* — badge shown on your card!" if action == "verify" else ""
            await _notify_user(tg_id,
                f"🎉 *Congratulations {name}!*\n\n"
                f"Your YourMeet profile has been *approved* ✅\n"
                f"You can now start swiping and matching!{verified_line}\n\n"
                f"Tap below to find your match 💕",
                {"inline_keyboard": [[{"text": "💕 Start Swiping", "url": app_url}]]}
            )
        else:
            await _notify_user(tg_id,
                f"🚫 *{name}*, your profile was rejected.\n\n"
                f"Please re-register with a clear photo and genuine bio.",
                {"inline_keyboard": [[{"text": "🔄 Re-register", "url": f"{app_url}/register"}]]}
            )
    status_line = f"\n\n{label}"
    try:
        await query.edit_message_caption(
            caption=(query.message.caption or "") + status_line,
            reply_markup=None
        )
    except:
        try:
            await query.edit_message_text(
                (query.message.text or "") + status_line,
                reply_markup=None
            )
        except: pass

def build_admin_app() -> Application:
    token = os.getenv("ADMIN_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("ADMIN_BOT_TOKEN is not set")
    request = HTTPXRequest(connect_timeout=20, read_timeout=20, write_timeout=20)
    app = ApplicationBuilder().token(token).request(request).updater(None).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pending", pending_cmd))
    app.add_handler(CommandHandler("remind", remind_cmd))
    app.add_handler(CommandHandler("remind_blocked", remind_blocked_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(verify_callback, pattern="^(approve|verify|block):"))
    return app
