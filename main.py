from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from database import init_db
from routers import auth, profiles, payment, admin
from dotenv import load_dotenv
import uvicorn, os

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOTS_KEY", "")
APP_URL = os.getenv("APP_URL", "")

bot_app = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Bot webhook setup
    global bot_app
    if BOT_TOKEN and APP_URL:
        from bot import build_app
        bot_app = build_app()
        await bot_app.initialize()
        webhook_url = f"{APP_URL}/webhook/{BOT_TOKEN}"
        await bot_app.bot.set_webhook(webhook_url)
        info = await bot_app.bot.get_webhook_info()
        print(f"[BOT] Webhook set: {info.url} | Pending: {info.pending_update_count}")
        await bot_app.start()
    else:
        print(f"[BOT] Skipped - BOT_TOKEN={'SET' if BOT_TOKEN else 'MISSING'}, APP_URL={'SET' if APP_URL else 'MISSING'}")
    yield
    if bot_app:
        await bot_app.stop()
        await bot_app.shutdown()

app = FastAPI(title="YourMeet", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth.router)
app.include_router(profiles.router)
app.include_router(payment.router)
app.include_router(admin.router)

@app.post("/webhook/{token}")
async def telegram_webhook(token: str, request: Request):
    if token != BOT_TOKEN or not bot_app:
        return JSONResponse({"error": "invalid"}, status_code=403)
    from telegram import Update
    data = await request.json()
    update = Update.de_json(data, bot_app.bot)
    await bot_app.process_update(update)
    return JSONResponse({"ok": True})

@app.api_route("/ping", methods=["GET", "HEAD"])
def ping():
    return JSONResponse({"status": "ok"})

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
