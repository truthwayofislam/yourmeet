import os
import warnings
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, LabeledPrice, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, PreCheckoutQueryHandler, ConversationHandler, filters, ContextTypes, Application
from telegram.warnings import PTBUserWarning
import secrets
import libsql_experimental as libsql

warnings.filterwarnings("ignore", category=PTBUserWarning)

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

BLOCKED_MSG = (
    "🚫 *Your profile was rejected.*\n\n"
    "Please re-register with a clear photo and genuine bio."
)

async def _check_blocked(update: Update, tg_id: str) -> bool:
    row = get_user_by_tg(tg_id)
    if row and row[15]:  # is_blocked
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Re-register", web_app=WebAppInfo(url=f"{APP_URL}/register"))]])
        msg = update.message or (update.callback_query.message if update.callback_query else None)
        if msg:
            await msg.reply_text(BLOCKED_MSG, parse_mode="Markdown", reply_markup=kb)
        return True
    return False

def open_app_keyboard(path=""):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("💕 Open YourMeet", web_app=WebAppInfo(url=f"{APP_URL}{path}"))
    ]])

MAIN_KB = ReplyKeyboardMarkup([
    [KeyboardButton("🔥 Swipe"), KeyboardButton("💕 Matches")],
    [KeyboardButton("👤 Profile"), KeyboardButton("📊 Stats")],
    [KeyboardButton("🔗 Share"), KeyboardButton("👑 Premium")],
    [KeyboardButton("🚀 Boost"), KeyboardButton("❓ Help")],
], resize_keyboard=True)

# ConversationHandler states
SETUP_NAME, SETUP_AGE, SETUP_GENDER, SETUP_CITY, SETUP_BIO, SETUP_PHOTO, SETUP_SOCIAL = range(7)
EDIT_CHOOSE, EDIT_VALUE, EDIT_PHOTO = range(7, 10)

# /setup - create profile in chat
async def setup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    row = get_user_by_tg(tg_id)
    if row and row[15]:  # blocked — delete old account so they can re-register
        conn = get_conn()
        old_id = row[0]
        conn.execute("DELETE FROM likes WHERE from_user=? OR to_user=?", (old_id, old_id))
        conn.execute("DELETE FROM matches WHERE user1_id=? OR user2_id=?", (old_id, old_id))
        conn.execute("DELETE FROM skips WHERE user_id=? OR skipped_id=?", (old_id, old_id))
        conn.execute("DELETE FROM users WHERE id=?", (old_id,))
        conn.commit()
        conn.close()
    elif row:
        await update.message.reply_text("✅ You already have a profile! Use /profile to view it.")
        return ConversationHandler.END
    await update.message.reply_text("👋 Let's create your profile!\n\nWhat's your *name*?", parse_mode="Markdown")
    return SETUP_NAME

async def setup_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("🎂 How old are you? (Enter age)")
    return SETUP_AGE

async def setup_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        age = int(update.message.text.strip())
        if age < 18 or age > 100:
            await update.message.reply_text("❌ Age must be between 18 and 100. Try again:")
            return SETUP_AGE
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number:")
        return SETUP_AGE
    context.user_data["age"] = age
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("👨 Male", callback_data="gender:male"),
        InlineKeyboardButton("👩 Female", callback_data="gender:female"),
    ]])
    await update.message.reply_text("⚧ Select your *gender*:", parse_mode="Markdown", reply_markup=kb)
    return SETUP_GENDER

async def setup_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["gender"] = query.data.split(":")[1]
    try:
        await query.edit_message_text(f"Gender set to *{context.user_data['gender']}* ✅", parse_mode="Markdown")
    except: pass
    await query.message.reply_text("📍 Which *city* are you from? (or type 'skip')")
    return SETUP_CITY

async def setup_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["city"] = "" if text.lower() == "skip" else text
    await update.message.reply_text("💬 Write a short *bio* about yourself (or type 'skip'):")
    return SETUP_BIO

async def setup_bio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["bio"] = "" if text.lower() == "skip" else text
    await update.message.reply_text("📸 Send your *profile photo* (required):", parse_mode="Markdown")
    return SETUP_PHOTO

async def setup_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("❌ Please send a *photo* (image file), not text!\n\nTap the 📎 attachment icon and select a photo.", parse_mode="Markdown")
        return SETUP_PHOTO
    file = await update.message.photo[-1].get_file()
    # Save full URL using bot token so it doesn't expire
    token = BOT_TOKEN
    photo_url = f"https://api.telegram.org/file/bot{token}/{file.file_path}"
    context.user_data["photo"] = photo_url
    await update.message.reply_text(
        "📱 Share your Instagram or Telegram username so matches can contact you\n"
        "Telegram: @rahul_tg\n"
        "Instagram: @rahul_ig or instagram.com/rahul\n\n"
        "Type 'skip' to leave blank:"
    )
    return SETUP_SOCIAL

async def setup_social(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    text = update.message.text.strip()
    social = "" if text.lower() == "skip" else text
    name = context.user_data["name"]
    age = context.user_data["age"]
    gender = context.user_data["gender"]
    city = context.user_data["city"]
    bio = context.user_data["bio"]
    photo = context.user_data["photo"]
    conn = get_conn()
    email = f"tg_{tg_id}@yourmeet.app"
    password = secrets.token_hex(16)
    conn.execute(
        "INSERT INTO users (name,email,password,age,gender,city,bio,photo,social_handle,telegram_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))",
        (name, email, password, age, gender, city, bio, photo, social, tg_id)
    )
    conn.commit()
    new_user = conn.execute("SELECT id FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
    conn.close()
    context.user_data.clear()
    # Notify admin bot for verification
    try:
        from admin_bot import send_for_review
        if new_user:
            await send_for_review(new_user[0], name, age, gender, city, photo, f"tg_{tg_id}@yourmeet.app", "")  # phone N/A for bot setup
    except Exception as e:
        print(f"[ADMIN NOTIFY] Failed: {e}")
    await update.message.reply_text(
        f"🎉 *Profile created!*\n\n"
        f"⏳ Your profile is under review. You'll get a message once approved!\n\n"
        f"Meanwhile, complete your profile on the app 👇",
        parse_mode="Markdown",
        reply_markup=open_app_keyboard("/")
    )
    return ConversationHandler.END

async def setup_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Profile setup cancelled.")
    return ConversationHandler.END

# /edit
async def edit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    if not get_user_by_tg(tg_id):
        await update.message.reply_text("❌ No profile found! Use /setup first.")
        return ConversationHandler.END
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📛 Name", callback_data="edit:name"), InlineKeyboardButton("🎂 Age", callback_data="edit:age")],
        [InlineKeyboardButton("📍 City", callback_data="edit:city"), InlineKeyboardButton("💬 Bio", callback_data="edit:bio")],
        [InlineKeyboardButton("📸 Photo", callback_data="edit:photo"), InlineKeyboardButton("📱 Social", callback_data="edit:social_handle")],
        [InlineKeyboardButton("❌ Cancel", callback_data="edit:cancel")],
    ])
    await update.message.reply_text("✏️ *What do you want to edit?*", parse_mode="Markdown", reply_markup=kb)
    return EDIT_CHOOSE

async def edit_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    field = query.data.split(":")[1]
    if field == "cancel":
        try:
            await query.edit_message_text("❌ Edit cancelled.")
        except: pass
        return ConversationHandler.END
    if field == "photo":
        try:
            await query.edit_message_text("📸 Send your new profile photo:")
        except: pass
        context.user_data["edit_field"] = "photo"
        return EDIT_PHOTO
    prompts = {
        "name": "📛 Enter your new name:",
        "age": "🎂 Enter your new age:",
        "city": "📍 Enter your new city:",
        "bio": "💬 Enter your new bio:",
        "social_handle": "📱 Enter your Instagram or Telegram username:\nTelegram: @rahul_tg\nInstagram: @rahul_ig or instagram.com/rahul",
    }
    context.user_data["edit_field"] = field
    try:
        await query.edit_message_text(prompts[field])
    except: pass
    return EDIT_VALUE

async def edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    field = context.user_data.get("edit_field")
    value = update.message.text.strip()
    if field == "age":
        try:
            value = int(value)
            if value < 18 or value > 100:
                await update.message.reply_text("❌ Age must be between 18 and 100. Try again:")
                return EDIT_VALUE
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid number:")
            return EDIT_VALUE
    conn = get_conn()
    allowed = {"name", "age", "city", "bio", "social_handle"}
    if field not in allowed:
        await update.message.reply_text("❌ Invalid field.")
        return ConversationHandler.END
    conn.execute(f"UPDATE users SET {field}=? WHERE telegram_id=?", (value, tg_id))
    conn.commit()
    conn.close()
    context.user_data.clear()
    await update.message.reply_text(f"✅ *{field.capitalize()}* updated successfully!", parse_mode="Markdown")
    return ConversationHandler.END

async def edit_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    if not update.message.photo:
        await update.message.reply_text("❌ Please send a photo:")
        return EDIT_PHOTO
    file = await update.message.photo[-1].get_file()
    photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
    conn = get_conn()
    conn.execute("UPDATE users SET photo=? WHERE telegram_id=?", (photo_url, tg_id))
    conn.commit()
    conn.close()
    context.user_data.clear()
    await update.message.reply_text("✅ *Photo* updated successfully!", parse_mode="Markdown")
    return ConversationHandler.END

def _check_swipe_limit(tg_id: str):
    """Returns (user_id, swipes_left, is_premium). Resets daily if needed."""
    from datetime import date
    today = str(date.today())
    conn = get_conn()
    row = conn.execute("SELECT id, is_premium, daily_swipes, swipes_reset_date FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
    if not row:
        conn.close()
        return None, 0, False
    user_id, is_premium, daily_swipes, reset_date = row
    if reset_date != today:
        daily_swipes = 3
        conn.execute("UPDATE users SET daily_swipes=3, swipes_reset_date=? WHERE id=?", (today, user_id))
        conn.commit()
    conn.close()
    return user_id, daily_swipes, bool(is_premium)

def _deduct_swipe(user_id: int):
    conn = get_conn()
    conn.execute("UPDATE users SET daily_swipes=daily_swipes-1 WHERE id=?", (user_id,))
    conn.commit()
    conn.close()

# /start - handles referral too
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    tg_id = str(user.id)
    if context.args and context.args[0].startswith("ref_"):
        try:
            referrer_id = int(context.args[0][4:])
            conn = get_conn()
            me = conn.execute("SELECT id FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
            already = me and conn.execute("SELECT id FROM referrals WHERE referred_id=?", (me[0],)).fetchone()
            if me and not already and me[0] != referrer_id:
                conn.execute("INSERT INTO referrals (referrer_id,referred_id,created_at) VALUES (?,?,datetime('now'))", (referrer_id, me[0]))
                conn.execute("UPDATE users SET referral_count=referral_count+1 WHERE id=?", (referrer_id,))
                ref_count = conn.execute("SELECT referral_count FROM users WHERE id=?", (referrer_id,)).fetchone()[0]
                conn.commit()
                if ref_count % 3 == 0:
                    conn.execute("UPDATE users SET daily_swipes=daily_swipes+10 WHERE id=?", (referrer_id,))
                    conn.commit()
                    ref_tg = conn.execute("SELECT telegram_id FROM users WHERE id=?", (referrer_id,)).fetchone()
                    if ref_tg and ref_tg[0]:
                        try:
                            await context.bot.send_message(ref_tg[0], "🎉 *+10 Swipes!* 3 friends joined using your link. Keep sharing!", parse_mode="Markdown")
                        except: pass
            conn.close()
        except: pass
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💕 Open YourMeet", web_app=WebAppInfo(url=APP_URL))],
        [InlineKeyboardButton("👑 Get Premium", web_app=WebAppInfo(url=f"{APP_URL}/premium"))]
    ])
    await update.message.reply_text(
        f"*Hey {user.first_name}! 👋*\n\n"
        f"Welcome to *YourMeet* 💕\n\n"
        f"🔥 Swipe & discover people\n"
        f"⭐ Send super likes\n"
        f"💕 Match & connect via Instagram/Telegram\n"
        f"👑 Go Premium for unlimited access\n\n"
        f"Tap below to open the app!",
        parse_mode="Markdown",
        reply_markup=kb
    )
    await update.message.reply_text("👇 Quick actions:", reply_markup=MAIN_KB)

# /profile
async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    if await _check_blocked(update, tg_id): return
    row = get_user_by_tg(tg_id)
    if not row:
        await update.message.reply_text("❌ Account not found. Please register first!", reply_markup=open_app_keyboard("/register"))
        return
    name, age, city, bio, is_premium = row[1], row[5], row[8], row[7], row[10]
    plan = "👑 Premium" if is_premium else "🆓 Free"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Edit Profile", web_app=WebAppInfo(url=f"{APP_URL}/profile")),
         InlineKeyboardButton("💕 Matches", web_app=WebAppInfo(url=f"{APP_URL}/matches"))],
        [InlineKeyboardButton("🔥 Start Swiping", callback_data="swipe_now")],
    ])
    await update.message.reply_text(
        f"👤 *Your Profile*\n\n"
        f"📛 Name: *{name}*\n"
        f"🎂 Age: *{age}*\n"
        f"📍 City: *{city or 'Not set'}*\n"
        f"💬 Bio: _{bio or 'Not set'}_\n"
        f"💎 Plan: *{plan}*",
        parse_mode="Markdown",
        reply_markup=kb
    )

# /matches
async def matches_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    if await _check_blocked(update, tg_id): return
    row = get_user_by_tg(tg_id)
    if not row:
        await update.message.reply_text("❌ Please register first!", reply_markup=open_app_keyboard("/register"))
        return
    user_id = row[0]
    conn = get_conn()
    count = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE user1_id=? OR user2_id=?", (user_id, user_id)
    ).fetchone()[0]
    conn.close()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💕 View Matches", web_app=WebAppInfo(url=f"{APP_URL}/matches")),
         InlineKeyboardButton("🔥 Swipe More", callback_data="swipe_now")],
    ])
    await update.message.reply_text(
        f"💕 *Your Matches*\n\n"
        f"You have *{count}* match{'es' if count != 1 else ''}!\n\n"
        f"{'Start chatting now 👇' if count > 0 else 'Keep swiping to get matches! 🔥'}",
        parse_mode="Markdown",
        reply_markup=kb
    )

def _swipe_keyboard(target_id: int):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👎 Nope", callback_data=f"nope:{target_id}"),
        InlineKeyboardButton("❤️ Like", callback_data=f"like:{target_id}"),
        InlineKeyboardButton("⭐ Super", callback_data=f"super:{target_id}"),
    ]])

def _record_skip(user_id: int, target_id: int):
    conn = get_conn()
    try:
        conn.execute("INSERT OR IGNORE INTO skips (user_id, skipped_id) VALUES (?,?)", (user_id, target_id))
        conn.commit()
    except: pass
    conn.close()

def _next_profile(tg_id: str):
    conn = get_conn()
    me = conn.execute("SELECT id, gender FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
    if not me:
        conn.close()
        return None, None
    user_id, gender = me
    opposite = "female" if gender == "male" else "male"
    liked = [r[0] for r in conn.execute("SELECT to_user FROM likes WHERE from_user=?", (user_id,)).fetchall()]
    skipped = [r[0] for r in conn.execute("SELECT skipped_id FROM skips WHERE user_id=?", (user_id,)).fetchall()]
    excluded = list(set(liked + skipped + [user_id]))
    placeholders = ",".join("?" * len(excluded))
    row = conn.execute(
        f"SELECT id, name, age, city, bio, photo FROM users WHERE id NOT IN ({placeholders}) AND gender=? AND age>=18 AND is_blocked=0 AND is_approved=1 LIMIT 1",
        (*excluded, opposite)
    ).fetchone()
    conn.close()
    return user_id, row

PROMO_MSG = (
    "💡 *Did you know?*\n\n"
    "All profiles on YourMeet are *real verified users* 🔒\n"
    "No bots, no fake accounts — just genuine people looking to connect 💕\n\n"
    "👉 Create your profile on the app for *better matches & more visibility!*"
)

async def _send_profile(send_fn, tg_id: str):
    me_id, row = _next_profile(tg_id)
    if not row:
        await send_fn("😔 No one left to swipe! Check back later.")
        return
    pid, name, age, city, bio, photo = row

    # Show promo every 3 swipes
    conn = get_conn()
    swipe_count = (conn.execute("SELECT COUNT(*) FROM likes WHERE from_user=?", (me_id,)).fetchone()[0] +
                   conn.execute("SELECT COUNT(*) FROM skips WHERE user_id=?", (me_id,)).fetchone()[0])
    conn.close()
    if swipe_count > 0 and swipe_count % 3 == 0:
        await send_fn(PROMO_MSG, parse_mode="Markdown", reply_markup=open_app_keyboard("/register"))

    caption = f"*{name}*, {age}" + (f" — 📍{city}" if city else "") + (f"\n_{bio}_" if bio else "")
    kb = _swipe_keyboard(pid)
    if photo and photo.startswith("https://"):
        await send_fn(photo, caption=caption, parse_mode="Markdown", reply_markup=kb, is_photo=True)
    else:
        await send_fn(f"👤 {caption}", parse_mode="Markdown", reply_markup=kb)

# /swipe
async def swipe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    if await _check_blocked(update, tg_id): return ConversationHandler.END
    row = get_user_by_tg(tg_id)
    if not row:
        await update.message.reply_text("👋 No profile found! Let's create one first.\n\nWhat's your *name*?", parse_mode="Markdown")
        return SETUP_NAME
    if not row[22]:  # is_approved
        await update.message.reply_text(
            "⏳ *Profile under review!*\n\nAdmin will approve your profile soon. You'll get a notification once approved!",
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    user_id, swipes_left, is_premium = _check_swipe_limit(tg_id)
    if not is_premium and swipes_left <= 0:
        await update.message.reply_text(
            "😔 *Daily limit reached!*\n\n"
            "You've used all 3 free swipes today.\n\n"
            "🔗 Share with 3 friends → get *+10 swipes*\nUse /share to get your link\n\n"
            "👑 Or go *Premium* for unlimited swipes!",
            parse_mode="Markdown", reply_markup=open_app_keyboard("/premium")
        )
        return ConversationHandler.END
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
    if await _check_blocked(update, tg_id): return
    action, target_id = query.data.split(":")
    target_id = int(target_id)

    conn = get_conn()
    me = conn.execute("SELECT id FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
    if not me:
        conn.close()
        await query.edit_message_caption(caption="❌ Account not found.")
        return
    user_id = me[0]

    if action in ("like", "super"):
        is_super = action == "super"
        user_id, swipes_left, is_premium = _check_swipe_limit(tg_id)
        if not is_premium and swipes_left <= 0:
            conn.close()
            try:
                await query.edit_message_caption(
                    caption="😔 *Daily limit reached!*\n\nUse /share to get +10 swipes or go Premium!",
                    parse_mode="Markdown"
                )
            except: pass
            return
        if not conn.execute("SELECT id FROM likes WHERE from_user=? AND to_user=?", (user_id, target_id)).fetchone():
            conn.execute("INSERT INTO likes (from_user,to_user,is_super,created_at) VALUES (?,?,?,datetime('now'))", (user_id, target_id, int(is_super)))
            conn.commit()
            if not is_premium:
                _deduct_swipe(user_id)
        mutual = conn.execute("SELECT id FROM likes WHERE from_user=? AND to_user=?", (target_id, user_id)).fetchone()
        if mutual:
            existing = conn.execute(
                "SELECT id FROM matches WHERE (user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?)",
                (user_id, target_id, target_id, user_id)
            ).fetchone()
            if not existing:
                conn.execute("INSERT INTO matches (user1_id,user2_id,matched_at) VALUES (?,?,datetime('now'))", (user_id, target_id))
                conn.commit()
                target_row = conn.execute("SELECT telegram_id, name, social_handle FROM users WHERE id=?", (target_id,)).fetchone()
                me_row = conn.execute("SELECT name, social_handle, is_premium FROM users WHERE id=?", (user_id,)).fetchone()
                me_name, me_social, me_premium = me_row
                # fetch target's premium status too
                target_premium_row = conn.execute("SELECT is_premium FROM users WHERE id=?", (target_id,)).fetchone()
                target_is_premium = bool(target_premium_row[0]) if target_premium_row else False
                conn.close()
                try:
                    await query.edit_message_caption(caption="💕 *It's a Match!* Keep swiping 🔥", parse_mode="Markdown")
                except: pass
                if target_row and target_row[0]:
                    try:
                        await notify_match(query.get_bot(), target_row[0], me_name, me_social, target_is_premium)
                    except: pass
                # also notify me about the target
                me_tg = conn if False else None  # already closed
                try:
                    me_tg_row = get_user_by_tg(tg_id)
                    if me_tg_row:
                        await notify_match(query.get_bot(), tg_id, target_row[1], target_row[2], bool(me_premium))
                except: pass
                return
        conn.close()
        emoji = "⭐" if is_super else "❤️"
        try:
            await query.edit_message_caption(caption=f"{emoji} Liked! See next 👇", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Next", callback_data="next")]]))
        except: pass
    elif action == "nope":
        _record_skip(user_id, target_id)
        conn.close()
        try:
            await query.edit_message_caption(caption="👎 Skipped! See next 👇", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Next", callback_data="next")]]))
        except: pass

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
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💫 1 Month — 150 ⭐", callback_data="buy:monthly")],
        [InlineKeyboardButton("🌟 3 Months — 350 ⭐", callback_data="buy:quarterly")],
    ])
    await update.message.reply_text(
        "👑 *YourMeet Premium*\n\n"
        "✅ Unlimited likes\n"
        "✅ Unlimited super likes\n"
        "✅ See who liked you\n"
        "✅ Profile boost\n"
        "✅ Priority in discover\n\n"
        "💫 *150 Stars/month* or *350 Stars/3 months*\n\n"
        "Choose a plan 👇",
        parse_mode="Markdown",
        reply_markup=kb
    )

async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    plan = query.data.split(":")[1]
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except: pass
    await send_stars_invoice(context.bot, tg_id, plan)

async def quick_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data
    if action == "swipe_now":
        tg_id = str(query.from_user.id)
        user_id, swipes_left, is_premium = _check_swipe_limit(tg_id)
        if not is_premium and swipes_left <= 0:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("👑 Go Premium", callback_data="buy:monthly")]])
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except: pass
            await query.message.reply_text(
                "😔 *Daily limit reached!*\n\nUse /share to get +10 swipes or go Premium!",
                parse_mode="Markdown", reply_markup=kb
            )
            return
        async def send_fn(content, caption=None, parse_mode=None, reply_markup=None, is_photo=False):
            if is_photo:
                await query.message.reply_photo(content, caption=caption, parse_mode=parse_mode, reply_markup=reply_markup)
            else:
                await query.message.reply_text(content, parse_mode=parse_mode, reply_markup=reply_markup)
        await _send_profile(send_fn, tg_id)
    elif action == "share_link":
        await share_cmd(update, context)
    elif action == "matches_now":
        await matches_cmd(update, context)
    elif action == "profile_now":
        await profile_cmd(update, context)
    elif action == "stats_now":
        await stats_cmd(update, context)
    elif action == "boost_now":
        await boost_cmd(update, context)

# /boost
async def boost_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    if await _check_blocked(update, tg_id): return
    row = get_user_by_tg(tg_id)
    if not row:
        await update.message.reply_text("❌ Please register first!", reply_markup=open_app_keyboard("/register"))
        return
    if not row[10]:  # is_premium
        await update.message.reply_text(
            "👑 *Boost is a Premium feature!*\n\nUpgrade to put your profile at the top of everyone's discover feed for 30 mins 🚀",
            parse_mode="Markdown", reply_markup=open_app_keyboard("/premium")
        )
        return
    from datetime import datetime, timedelta
    until = (datetime.utcnow() + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    conn.execute("UPDATE users SET boosted_until=? WHERE telegram_id=?", (until, tg_id))
    conn.commit()
    conn.close()
    await update.message.reply_text(
        "🚀 *Profile Boosted!*\n\nYour profile is now at the top of discover for *30 minutes* 🔥\n\nGo get those matches!",
        parse_mode="Markdown"
    )

# /stats
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    if await _check_blocked(update, tg_id): return
    row = get_user_by_tg(tg_id)
    if not row:
        await update.message.reply_text("❌ Please register first!", reply_markup=open_app_keyboard("/register"))
        return
    user_id = row[0]
    conn = get_conn()
    likes_given = conn.execute("SELECT COUNT(*) FROM likes WHERE from_user=?", (user_id,)).fetchone()[0]
    likes_received = conn.execute("SELECT COUNT(*) FROM likes WHERE to_user=?", (user_id,)).fetchone()[0]
    matches = conn.execute("SELECT COUNT(*) FROM matches WHERE user1_id=? OR user2_id=?", (user_id, user_id)).fetchone()[0]
    conn.close()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Keep Swiping", callback_data="swipe_now"),
         InlineKeyboardButton("👑 Go Premium", callback_data="buy:monthly")],
        [InlineKeyboardButton("🔗 Share & Get Swipes", callback_data="share_link")],
    ])
    await update.message.reply_text(
        f"📊 *Your Stats*\n\n"
        f"❤️ Likes Given: *{likes_given}*\n"
        f"💌 Likes Received: *{likes_received}*\n"
        f"💕 Matches: *{matches}*",
        parse_mode="Markdown",
        reply_markup=kb
    )

# /delete
async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    if not get_user_by_tg(tg_id):
        await update.message.reply_text("❌ Account not found.")
        return
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⚠️ Yes, Delete", callback_data="confirm_delete"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_delete")
    ]])
    await update.message.reply_text(
        "⚠️ *Account Delete*\n\n"
        "Are you sure? This cannot be undone.\n"
        "All your matches and messages will be lost.",
        parse_mode="Markdown",
        reply_markup=kb
    )

async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg_id = str(query.from_user.id)
    if query.data == "cancel_delete":
        try:
            await query.edit_message_text("✅ Cancelled. Your account is safe!")
        except: pass
        return
    conn = get_conn()
    me = conn.execute("SELECT id FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
    if not me:
        conn.close()
        try:
            await query.edit_message_text("❌ Account not found.")
        except: pass
        return
    user_id = me[0]
    conn.execute("DELETE FROM matches WHERE user1_id=? OR user2_id=?", (user_id, user_id))
    conn.execute("DELETE FROM likes WHERE from_user=? OR to_user=?", (user_id, user_id))
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    try:
        await query.edit_message_text("🗑️ Account deleted. Goodbye! 👋")
    except: pass

# /share
async def share_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    if await _check_blocked(update, tg_id): return
    row = get_user_by_tg(tg_id)
    if not row:
        await update.message.reply_text("❌ Please register first!", reply_markup=open_app_keyboard("/register"))
        return
    user_id = row[0]
    referral_count = row[18] if len(row) > 18 else 0
    link = f"https://t.me/Yoursmeetbot?start=ref_{user_id}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Share Link", url=f"https://t.me/share/url?url={link}&text=Join%20me%20on%20YourMeet%20💕")],
        [InlineKeyboardButton("🔥 Start Swiping", callback_data="swipe_now")],
    ])
    await update.message.reply_text(
        f"🔗 *Your Referral Link*\n\n"
        f"`{link}`\n\n"
        f"Share this with friends!\n"
        f"Every *3 friends* who join = *+10 swipes* for you 🎉\n\n"
        f"👥 Friends joined so far: *{referral_count}*",
        parse_mode="Markdown",
        reply_markup=kb
    )

# /help
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Swipe", callback_data="swipe_now"),
         InlineKeyboardButton("💕 Matches", callback_data="matches_now")],
        [InlineKeyboardButton("👤 Profile", callback_data="profile_now"),
         InlineKeyboardButton("🔗 Share", callback_data="share_link")],
        [InlineKeyboardButton("👑 Premium", callback_data="buy:monthly"),
         InlineKeyboardButton("🚀 Boost", callback_data="boost_now")],
        [InlineKeyboardButton("📊 Stats", callback_data="stats_now"),
         InlineKeyboardButton("📱 Open App", web_app=WebAppInfo(url=APP_URL))],
    ])
    await update.message.reply_text(
        "🌹 *YourMeet Commands*\n\n"
        "/start - Open the app\n"
        "/setup - Create your profile in chat\n"
        "/edit - Edit your profile\n"
        "/profile - View your profile\n"
        "/matches - See your matches\n"
        "/swipe - Swipe in chat (no app needed)\n"
        "/share - Get referral link (+10 swipes per 3 friends)\n"
        "/boost - Boost your profile to top (Premium)\n"
        "/stats - Your activity stats\n"
        "/premium - Get Premium\n"
        "/delete - Delete account\n"
        "/help - Show this message",
        parse_mode="Markdown",
        reply_markup=kb
    )

STARS_PLANS = {
    "monthly":   {"stars": 150, "title": "YourMeet Premium — 1 Month",  "description": "Unlimited likes, super likes & more for 30 days"},
    "quarterly": {"stars": 350, "title": "YourMeet Premium — 3 Months", "description": "Unlimited likes, super likes & more for 90 days"},
}

async def send_stars_invoice(bot, tg_id: str, plan: str) -> str:
    """Send a Telegram Stars invoice and return the invoice link."""
    p = STARS_PLANS.get(plan, STARS_PLANS["monthly"])
    msg = await bot.send_invoice(
        chat_id=tg_id,
        title=p["title"],
        description=p["description"],
        payload=f"premium:{plan}:{tg_id}",
        currency="XTR",
        prices=[LabeledPrice(label=p["title"], amount=p["stars"])],
        provider_token="",
    )
    return msg.invoice.start_parameter if hasattr(msg, 'invoice') else ""

async def handle_pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def handle_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = str(update.effective_user.id)
    payload = update.message.successful_payment.invoice_payload
    plan = payload.split(":")[1] if ":" in payload else "monthly"
    from datetime import datetime, timedelta
    days = 90 if plan == "quarterly" else 30
    premium_until = (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    conn.execute("UPDATE users SET is_premium=1, super_likes_left=999, premium_until=? WHERE telegram_id=?", (premium_until, tg_id))
    conn.commit()
    conn.close()
    await update.message.reply_text(
        "🎉 *Welcome to Premium!* 👑\n\n"
        "✅ Unlimited likes\n"
        "✅ Unlimited super likes\n"
        "✅ See who liked you\n\n"
        "Enjoy! 💕",
        parse_mode="Markdown",
        reply_markup=open_app_keyboard("/")
    )

async def notify_match(bot, tg_id: str, matched_name: str, social_handle: str = "", is_premium: bool = False):
    if not tg_id:
        return
    try:
        if is_premium and social_handle:
            contact_line = f"\n\n📱 Contact: *{social_handle}*"
        else:
            contact_line = "\n\n🔒 *Upgrade to Premium* to see contact details!"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💕 View Matches", web_app=WebAppInfo(url=f"{APP_URL}/matches"))],
        ]) if is_premium else InlineKeyboardMarkup([
            [InlineKeyboardButton("💕 View Matches", web_app=WebAppInfo(url=f"{APP_URL}/matches"))],
            [InlineKeyboardButton("👑 Unlock Contact — Premium", callback_data="buy:monthly")],
        ])
        await bot.send_message(
            chat_id=tg_id,
            text=f"💕 *It's a Match!*\n\nYou and *{matched_name}* liked each other!{contact_line}",
            parse_mode="Markdown",
            reply_markup=kb
        )
    except:
        pass

async def keyboard_btn_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("edit_field"):  # edit conversation active
        return
    text = update.message.text
    if text == "🔥 Swipe":
        await swipe_cmd(update, context)
    elif text == "💕 Matches":
        await matches_cmd(update, context)
    elif text == "👤 Profile":
        await profile_cmd(update, context)
    elif text == "📊 Stats":
        await stats_cmd(update, context)
    elif text == "🔗 Share":
        await share_cmd(update, context)
    elif text == "👑 Premium":
        await premium_cmd(update, context)
    elif text == "🚀 Boost":
        await boost_cmd(update, context)
    elif text == "❓ Help":
        await help_cmd(update, context)

from telegram.request import HTTPXRequest

def build_app() -> Application:
    request = HTTPXRequest(connect_timeout=20, read_timeout=20, write_timeout=20)
    app = ApplicationBuilder().token(BOT_TOKEN).request(request).updater(None).build()
    setup_conv = ConversationHandler(
        entry_points=[CommandHandler("setup", setup_cmd), CommandHandler("swipe", swipe_cmd)],
        states={
            SETUP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_name)],
            SETUP_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_age)],
            SETUP_GENDER: [CallbackQueryHandler(setup_gender, pattern="^gender:")],
            SETUP_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_city)],
            SETUP_BIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_bio)],
            SETUP_PHOTO: [MessageHandler(filters.PHOTO, setup_photo), MessageHandler(filters.TEXT & ~filters.COMMAND, setup_photo)],
            SETUP_SOCIAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_social)],
        },
        fallbacks=[CommandHandler("cancel", setup_cancel), CommandHandler("setup", setup_cmd)],
        per_message=False,
        allow_reentry=True,
    )
    edit_conv = ConversationHandler(
        entry_points=[CommandHandler("edit", edit_cmd)],
        states={
            EDIT_CHOOSE: [CallbackQueryHandler(edit_choose, pattern="^edit:")],
            EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value)],
            EDIT_PHOTO: [MessageHandler(filters.PHOTO, edit_photo)],
        },
        fallbacks=[CommandHandler("cancel", setup_cancel)],
        per_message=False,
    )
    app.add_handler(edit_conv)
    app.add_handler(setup_conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("profile", profile_cmd))
    app.add_handler(CommandHandler("matches", matches_cmd))
    app.add_handler(CommandHandler("like", like_cmd))
    app.add_handler(CallbackQueryHandler(swipe_callback, pattern="^(like|nope|super):"))
    app.add_handler(CallbackQueryHandler(next_callback, pattern="^next$"))
    app.add_handler(CallbackQueryHandler(delete_callback, pattern="^(confirm_delete|cancel_delete)$"))
    app.add_handler(CallbackQueryHandler(buy_callback, pattern="^buy:"))
    app.add_handler(CallbackQueryHandler(quick_action_callback, pattern="^(swipe_now|share_link|matches_now|profile_now|stats_now|boost_now)$"))
    app.add_handler(CommandHandler("friends", friends_cmd))
    app.add_handler(CommandHandler("boost", boost_cmd))
    app.add_handler(CommandHandler("premium", premium_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("share", share_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(PreCheckoutQueryHandler(handle_pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, handle_successful_payment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, keyboard_btn_handler))
    return app
