import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, Application
import libsql_experimental as libsql

BOT_TOKEN = os.getenv("TELEGRAM_BOTS_KEY", "")
APP_URL = os.getenv("APP_URL", "")
TURSO_URL = os.getenv("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.getenv("TURSO_DATABASE_KEY", "")

def get_conn():
    if TURSO_URL and TURSO_TOKEN:
        return libsql.connect(TURSO_URL, auth_token=TURSO_TOKEN)
    return libsql.connect("yourmeet.db")

def get_user_by_tg(tg_id: str):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
    conn.close()
    return row

def open_app_keyboard(path=""):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("💕 Open YourMeet", web_app=WebAppInfo(url=f"{APP_URL}{path}"))
    ]])

MINI_APP_URL = "https://t.me/Yoursmeetbot/YourMeet"

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💕 Open YourMeet", web_app=WebAppInfo(url=APP_URL))],
        [InlineKeyboardButton("👑 Get Premium", web_app=WebAppInfo(url=f"{APP_URL}/premium"))]
    ])
    await update.message.reply_text(
        f"*Hey {user.first_name}! 👋*\n\n"
        f"Welcome to *YourMeet* 💕\n\n"
        f"🔥 Swipe & discover people\n"
        f"💬 Chat with your matches\n"
        f"⭐ Send super likes\n"
        f"👫 Meet new friends\n"
        f"👑 Go Premium for unlimited access\n\n"
        f"Tap below to open the app!",
        parse_mode="Markdown",
        reply_markup=kb
    )

# /profile
async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    row = get_user_by_tg(tg_id)
    if not row:
        await update.message.reply_text("❌ Account nahi mila. Pehle register karo!", reply_markup=open_app_keyboard("/register"))
        return
    name, age, city, bio, is_premium = row[1], row[5], row[8], row[7], row[10]
    plan = "👑 Premium" if is_premium else "🆓 Free"
    await update.message.reply_text(
        f"👤 *Your Profile*\n\n"
        f"📛 Name: *{name}*\n"
        f"🎂 Age: *{age}*\n"
        f"📍 City: *{city or 'Not set'}*\n"
        f"💬 Bio: _{bio or 'Not set'}_\n"
        f"💎 Plan: *{plan}*\n\n"
        f"Edit your profile in the app 👇",
        parse_mode="Markdown",
        reply_markup=open_app_keyboard("/profile")
    )

# /matches
async def matches_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    row = get_user_by_tg(tg_id)
    if not row:
        await update.message.reply_text("❌ Pehle register karo!", reply_markup=open_app_keyboard("/register"))
        return
    user_id = row[0]
    conn = get_conn()
    count = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE user1_id=? OR user2_id=?", (user_id, user_id)
    ).fetchone()[0]
    conn.close()
    await update.message.reply_text(
        f"💕 *Your Matches*\n\n"
        f"You have *{count}* match{'es' if count != 1 else ''}!\n\n"
        f"{'Start chatting now 👇' if count > 0 else 'Keep swiping to get matches! 🔥'}",
        parse_mode="Markdown",
        reply_markup=open_app_keyboard("/matches")
    )

def _swipe_keyboard(target_id: int):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👎 Nope", callback_data=f"nope:{target_id}"),
        InlineKeyboardButton("❤️ Like", callback_data=f"like:{target_id}"),
        InlineKeyboardButton("⭐ Super", callback_data=f"super:{target_id}"),
    ]])

def _next_profile(tg_id: str):
    conn = get_conn()
    me = conn.execute("SELECT id, gender FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
    if not me:
        conn.close()
        return None, None
    user_id, gender = me
    opposite = "female" if gender == "male" else "male"
    liked = [r[0] for r in conn.execute("SELECT to_user FROM likes WHERE from_user=?", (user_id,)).fetchall()]
    liked.append(user_id)
    placeholders = ",".join("?" * len(liked))
    row = conn.execute(
        f"SELECT id, name, age, city, bio, photo FROM users WHERE id NOT IN ({placeholders}) AND gender=? AND age>=18 AND is_blocked=0 LIMIT 1",
        (*liked, opposite)
    ).fetchone()
    conn.close()
    return user_id, row

async def _send_profile(send_fn, tg_id: str):
    me_id, row = _next_profile(tg_id)
    if not row:
        await send_fn("😔 Abhi koi nahi mila. Baad mein try karo!")
        return
    pid, name, age, city, bio, photo = row
    caption = f"*{name}*, {age}" + (f" — 📍{city}" if city else "") + (f"\n_{bio}_" if bio else "")
    kb = _swipe_keyboard(pid)
    if photo and photo.startswith("https://"):
        await send_fn(photo, caption=caption, parse_mode="Markdown", reply_markup=kb, is_photo=True)
    else:
        await send_fn(f"👤 {caption}", parse_mode="Markdown", reply_markup=kb)

# /swipe
async def swipe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    if not get_user_by_tg(tg_id):
        await update.message.reply_text("❌ Pehle register karo!", reply_markup=open_app_keyboard("/register"))
        return
    async def send_fn(content, caption=None, parse_mode=None, reply_markup=None, is_photo=False):
        if is_photo:
            await update.message.reply_photo(content, caption=caption, parse_mode=parse_mode, reply_markup=reply_markup)
        else:
            await update.message.reply_text(content, parse_mode=parse_mode, reply_markup=reply_markup)
    await _send_profile(send_fn, tg_id)

# callback: like / nope / super
async def swipe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    action, target_id = query.data.split(":")
    target_id = int(target_id)

    conn = get_conn()
    me = conn.execute("SELECT id FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
    if not me:
        conn.close()
        await query.edit_message_caption(caption="❌ Account nahi mila.")
        return
    user_id = me[0]

    if action in ("like", "super"):
        is_super = action == "super"
        if not conn.execute("SELECT id FROM likes WHERE from_user=? AND to_user=?", (user_id, target_id)).fetchone():
            conn.execute("INSERT INTO likes (from_user,to_user,is_super,created_at) VALUES (?,?,?,datetime('now'))", (user_id, target_id, int(is_super)))
            conn.commit()
        mutual = conn.execute("SELECT id FROM likes WHERE from_user=? AND to_user=?", (target_id, user_id)).fetchone()
        if mutual:
            existing = conn.execute(
                "SELECT id FROM matches WHERE (user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?)",
                (user_id, target_id, target_id, user_id)
            ).fetchone()
            if not existing:
                conn.execute("INSERT INTO matches (user1_id,user2_id,matched_at) VALUES (?,?,datetime('now'))", (user_id, target_id))
                conn.commit()
                target_row = conn.execute("SELECT telegram_id, name FROM users WHERE id=?", (target_id,)).fetchone()
                me_name = conn.execute("SELECT name FROM users WHERE id=?", (user_id,)).fetchone()[0]
                conn.close()
                await query.edit_message_caption(caption="💕 *It's a Match!* Swipe karte raho 🔥", parse_mode="Markdown")
                if target_row and target_row[0]:
                    try:
                        await notify_match(query.get_bot(), target_row[0], me_name)
                    except: pass
                return
        conn.close()
        emoji = "⭐" if is_super else "❤️"
        await query.edit_message_caption(caption=f"{emoji} Liked! Next dekho 👇", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Next", callback_data="next")]]))
    elif action == "nope":
        conn.close()
        await query.edit_message_caption(caption="👎 Skipped! Next dekho 👇", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Next", callback_data="next")]]))

# callback: next
async def next_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    async def send_fn(content, caption=None, parse_mode=None, reply_markup=None, is_photo=False):
        if is_photo:
            await query.message.reply_photo(content, caption=caption, parse_mode=parse_mode, reply_markup=reply_markup)
        else:
            await query.message.reply_text(content, parse_mode=parse_mode, reply_markup=reply_markup)
    await _send_profile(send_fn, tg_id)

# /like - open discover page
async def like_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔥 *Discover People*\n\n"
        "Swipe right to like, left to pass!\n"
        "⭐ Super like to stand out!\n\n"
        "Open the app to start swiping 👇",
        parse_mode="Markdown",
        reply_markup=open_app_keyboard("/")
    )

# /friends - same as discover but framed as friends
async def friends_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👫 *Meet New Friends*\n\n"
        "Discover amazing people near you!\n"
        "Connect, chat and make new friends 🌟\n\n"
        "Open the app 👇",
        parse_mode="Markdown",
        reply_markup=open_app_keyboard("/")
    )

# /premium
async def premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    row = get_user_by_tg(tg_id)
    if row and row[10]:
        await update.message.reply_text("👑 *You are already Premium!*\n\nEnjoy unlimited access 🎉", parse_mode="Markdown")
        return
    await update.message.reply_text(
        "👑 *YourMeet Premium*\n\n"
        "✅ Unlimited likes\n"
        "✅ Unlimited super likes\n"
        "✅ See who liked you\n"
        "✅ Profile boost\n"
        "✅ Priority in discover\n\n"
        "💰 *₹50/month* or *₹120/3 months*\n\n"
        "Upgrade now 👇",
        parse_mode="Markdown",
        reply_markup=open_app_keyboard("/premium")
    )

# /stats - user ka stats
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    row = get_user_by_tg(tg_id)
    if not row:
        await update.message.reply_text("❌ Pehle register karo!", reply_markup=open_app_keyboard("/register"))
        return
    user_id = row[0]
    conn = get_conn()
    likes_given = conn.execute("SELECT COUNT(*) FROM likes WHERE from_user=?", (user_id,)).fetchone()[0]
    likes_received = conn.execute("SELECT COUNT(*) FROM likes WHERE to_user=?", (user_id,)).fetchone()[0]
    matches = conn.execute("SELECT COUNT(*) FROM matches WHERE user1_id=? OR user2_id=?", (user_id, user_id)).fetchone()[0]
    messages = conn.execute("SELECT COUNT(*) FROM messages WHERE sender_id=?", (user_id,)).fetchone()[0]
    conn.close()
    await update.message.reply_text(
        f"📊 *Your Stats*\n\n"
        f"❤️ Likes Given: *{likes_given}*\n"
        f"💌 Likes Received: *{likes_received}*\n"
        f"💕 Matches: *{matches}*\n"
        f"💬 Messages Sent: *{messages}*\n",
        parse_mode="Markdown"
    )

# /delete
async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    if not get_user_by_tg(tg_id):
        await update.message.reply_text("❌ Account nahi mila.")
        return
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⚠️ Haan, Delete Karo", callback_data="confirm_delete"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_delete")
    ]])
    await update.message.reply_text(
        "⚠️ *Account Delete*\n\n"
        "Pakka karna hai? Ye undo nahi hoga.\n"
        "Saare matches aur messages delete ho jayenge.",
        parse_mode="Markdown",
        reply_markup=kb
    )

async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if query.data == "cancel_delete":
        await query.edit_message_text("✅ Cancel ho gaya. Account safe hai!")
        return
    conn = get_conn()
    me = conn.execute("SELECT id FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
    if not me:
        conn.close()
        await query.edit_message_text("❌ Account nahi mila.")
        return
    user_id = me[0]
    conn.execute("DELETE FROM messages WHERE sender_id=? OR receiver_id=?", (user_id, user_id))
    conn.execute("DELETE FROM matches WHERE user1_id=? OR user2_id=?", (user_id, user_id))
    conn.execute("DELETE FROM likes WHERE from_user=? OR to_user=?", (user_id, user_id))
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    await query.edit_message_text("🗑️ Account delete ho gaya. Bye bye! 👋")

# /help
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌹 *YourMeet Commands*\n\n"
        "/start - Open the app\n"
        "/profile - View your profile\n"
        "/matches - See your matches\n"
                "/swipe - Swipe in chat (no app needed)\n"
        "/like - Discover people\n"
        "/friends - Meet new friends\n"
        "/stats - Your activity stats\n"
        "/premium - Get Premium\n"
        "/delete - Delete account\n"
        "/help - Show this message",
        parse_mode="Markdown",
        reply_markup=open_app_keyboard()
    )

async def notify_match(bot, tg_id: str, matched_name: str):
    """Call this when a match happens"""
    if not tg_id:
        return
    try:
        await bot.send_message(
            chat_id=tg_id,
            text=f"💕 *It's a Match!*\n\nYou and *{matched_name}* liked each other!\n\nSay hello now 👇",
            parse_mode="Markdown",
            reply_markup=open_app_keyboard("/matches")
        )
    except:
        pass

async def notify_message(bot, tg_id: str, sender_name: str):
    """Call this when a new message arrives"""
    if not tg_id:
        return
    try:
        await bot.send_message(
            chat_id=tg_id,
            text=f"💬 *New Message!*\n\n*{sender_name}* sent you a message!\n\nReply now 👇",
            parse_mode="Markdown",
            reply_markup=open_app_keyboard("/matches")
        )
    except:
        pass

def build_app() -> Application:
    app = ApplicationBuilder().token(BOT_TOKEN).updater(None).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("profile", profile_cmd))
    app.add_handler(CommandHandler("matches", matches_cmd))
    app.add_handler(CommandHandler("swipe", swipe_cmd))
    app.add_handler(CommandHandler("like", like_cmd))
    app.add_handler(CallbackQueryHandler(swipe_callback, pattern="^(like|nope|super):"))
    app.add_handler(CallbackQueryHandler(next_callback, pattern="^next$"))
    app.add_handler(CallbackQueryHandler(delete_callback, pattern="^(confirm_delete|cancel_delete)$"))
    app.add_handler(CommandHandler("friends", friends_cmd))
    app.add_handler(CommandHandler("premium", premium_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    return app
