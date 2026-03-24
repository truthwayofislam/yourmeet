from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from database import init_db
from routers import auth, profiles, payment, admin
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import uvicorn, os, httpx

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOTS_KEY", "")
APP_URL = os.getenv("APP_URL", "")

bot_app = None
admin_bot_app = None

async def send_incomplete_reminders():
    from database import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT telegram_id, name, photo, bio, city, social_handle FROM users "
        "WHERE is_admin=0 AND is_blocked=0 AND telegram_id IS NOT NULL AND telegram_id != '' "
        "AND (photo='' OR bio='' OR city='' OR social_handle='')"
    ).fetchall()
    conn.close()
    bot_token = os.getenv("TELEGRAM_BOTS_KEY", "")
    app_url = os.getenv("APP_URL", "")
    if not bot_token or not rows:
        return
    api = f"https://api.telegram.org/bot{bot_token}"
    import json
    async with httpx.AsyncClient(timeout=20) as client:
        for tg_id, name, photo, bio, city, social in rows:
            missing = []
            if not photo: missing.append("📸 Profile photo")
            if not bio: missing.append("💬 Bio")
            if not city: missing.append("📍 City")
            if not social: missing.append("📱 Instagram/Telegram handle")
            if not missing:
                continue
            text = (
                f"👋 Hey *{name or 'there'}*!\n\n"
                f"Your YourMeet profile is incomplete. Add these to get *real matches*:\n\n"
                + "\n".join(f"• {m}" for m in missing)
                + "\n\nComplete your profile now 👇"
            )
            keyboard = {"inline_keyboard": [[{"text": "✏️ Complete Profile", "url": f"{app_url}/profile"}]]}
            try:
                await client.post(f"{api}/sendMessage", json={
                    "chat_id": tg_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "reply_markup": keyboard
                })
            except: pass

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
    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_incomplete_reminders, "cron", hour=14, minute=30)  # 8 PM IST
    scheduler.start()
    print("[SCHEDULER] Incomplete profile reminder scheduled at 8 PM IST daily")
    yield
    if bot_app:
        await bot_app.stop()
        await bot_app.shutdown()
    if admin_bot_app:
        await admin_bot_app.stop()
        await admin_bot_app.shutdown()
    scheduler.shutdown()

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
