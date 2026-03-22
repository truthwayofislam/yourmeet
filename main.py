from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager
from database import init_db
from routers import auth, profiles, payment, admin
from storage import get_photo_url
from dotenv import load_dotenv
import uvicorn, os

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOTS_KEY", "")
APP_URL = os.getenv("APP_URL", "")

bot_app = None
admin_bot_app = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    global bot_app, admin_bot_app
    if BOT_TOKEN and APP_URL:
        from bot import build_app
        bot_app = build_app()
        await bot_app.initialize()
        await bot_app.bot.set_webhook(f"{APP_URL}/webhook/{BOT_TOKEN}")
        await bot_app.start()
        print(f"[BOT] Webhook set")
    else:
        print(f"[BOT] Skipped - BOT_TOKEN={'SET' if BOT_TOKEN else 'MISSING'}, APP_URL={'SET' if APP_URL else 'MISSING'}")
    ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN", "")
    if ADMIN_BOT_TOKEN and APP_URL:
        from admin_bot import build_admin_app
        admin_bot_app = build_admin_app()
        await admin_bot_app.initialize()
        await admin_bot_app.bot.set_webhook(f"{APP_URL}/admin-webhook/{ADMIN_BOT_TOKEN}")
        await admin_bot_app.start()
        print(f"[ADMIN BOT] Webhook set")
    yield
    if bot_app:
        await bot_app.stop()
        await bot_app.shutdown()
    if admin_bot_app:
        await admin_bot_app.stop()
        await admin_bot_app.shutdown()

app = FastAPI(title="YourMeet", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth.router)
app.include_router(profiles.router)
app.include_router(payment.router)
app.include_router(admin.router)

templates = Jinja2Templates(directory="templates")
templates.env.filters["photo_url"] = get_photo_url

@app.post("/webhook/{token}")
async def telegram_webhook(token: str, request: Request):
    if token != BOT_TOKEN or not bot_app:
        return JSONResponse({"error": "invalid"}, status_code=403)
    from telegram import Update
    update = Update.de_json(await request.json(), bot_app.bot)
    await bot_app.process_update(update)
    return JSONResponse({"ok": True})

@app.post("/admin-webhook/{token}")
async def admin_telegram_webhook(token: str, request: Request):
    ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN", "")
    if token != ADMIN_BOT_TOKEN or not admin_bot_app:
        return JSONResponse({"error": "invalid"}, status_code=403)
    from telegram import Update
    update = Update.de_json(await request.json(), admin_bot_app.bot)
    await admin_bot_app.process_update(update)
    return JSONResponse({"ok": True})

@app.api_route("/ping", methods=["GET", "HEAD"])
def ping():
    return JSONResponse({"status": "ok"})

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
