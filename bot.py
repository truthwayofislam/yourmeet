import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("TELEGRAM_BOT_KEY", "")
APP_URL = os.getenv("APP_URL", "")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    keyboard = [[
        InlineKeyboardButton(
            "💕 Open YourMeet",
            web_app=WebAppInfo(url=APP_URL)
        )
    ]]
    await update.message.reply_text(
        f"*Hey {user.first_name}! 👋*\n\nWelcome to *YourMeet* — Find your perfect match!\n\n"
        f"✨ Swipe profiles\n💬 Chat with matches\n⭐ Super likes\n👑 Premium features\n\n"
        f"Tap below to start!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌹 *YourMeet Commands*\n\n"
        "/start - Open the app\n"
        "/premium - See premium plans\n"
        "/help - Show this message",
        parse_mode="Markdown"
    )

async def premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("👑 Get Premium", web_app=WebAppInfo(url=f"{APP_URL}/premium"))]]
    await update.message.reply_text(
        "👑 *YourMeet Premium*\n\n"
        "✅ Unlimited likes\n"
        "✅ Unlimited super likes\n"
        "✅ See who liked you\n"
        "✅ Profile boost\n\n"
        "💰 *₹299/month* or *₹699/3 months*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def run_bot():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("premium", premium_cmd))
    print("✅ YourMeet Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    run_bot()
