import os
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

from database import init_db, get_conn
from routers import auth, setup, profiles, map, chat, payment, translate
from routers.auth import get_current_user

BOT_TOKEN = os.getenv("TELEGRAM_BOTS_KEY", "")
ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN", "")
APP_URL = os.getenv("APP_URL", "")

bot_app = None
admin_bot_app = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_app, admin_bot_app

    # Init DB
    init_db()

    # Start main bot
    if BOT_TOKEN and APP_URL:
        from bot import build_bot
        bot_app = build_bot()
        await bot_app.initialize()
        await bot_app.bot.set_webhook(
            f"{APP_URL}/webhook/{BOT_TOKEN}",
            drop_pending_updates=True,
        )
        await bot_app.start()
        print(f"[BOT] Webhook set")
    else:
        print(f"[BOT] Skipped — BOT_TOKEN or APP_URL missing")

    # Start admin bot
    if ADMIN_BOT_TOKEN and APP_URL:
        from admin_bot import build_admin_bot
        admin_bot_app = build_admin_bot()
        await admin_bot_app.initialize()
        await admin_bot_app.bot.set_webhook(
            f"{APP_URL}/admin-webhook/{ADMIN_BOT_TOKEN}",
            drop_pending_updates=True,
        )
        await admin_bot_app.start()
        print(f"[ADMIN BOT] Webhook set")
    else:
        print(f"[ADMIN BOT] Skipped — ADMIN_BOT_TOKEN or APP_URL missing")

    # Scheduler — cleanup expired chat sessions every minute
    scheduler = AsyncIOScheduler()
    scheduler.add_job(_cleanup_chats, "interval", minutes=1)
    scheduler.start()
    print("[SCHEDULER] Started")

    yield

    if bot_app:
        await bot_app.stop()
        await bot_app.shutdown()
    if admin_bot_app:
        await admin_bot_app.stop()
        await admin_bot_app.shutdown()
    scheduler.shutdown()


async def _cleanup_chats():
    from routers.chat import cleanup_expired_sessions
    db = get_conn()
    await cleanup_expired_sessions(db)


app = FastAPI(title="YourMeet", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth.router)
app.include_router(setup.router)
app.include_router(profiles.router)
app.include_router(map.router)
app.include_router(chat.router)
app.include_router(payment.router)
app.include_router(translate.router)


@app.get("/premium")
async def premium_page(request: Request, current_user=Depends(get_current_user)):
    from fastapi.responses import HTMLResponse, RedirectResponse
    from templating import templates
    if not current_user:
        return RedirectResponse("/setup")
    return templates.TemplateResponse(request, "premium.html", {"user": current_user, "active": "premium"})


@app.get("/photo/{file_id:path}")
async def proxy_photo(file_id: str):
    """Proxy Telegram file_id to image bytes."""
    import httpx
    bot_token = os.getenv("TELEGRAM_BOTS_KEY", "").strip().strip("'\"")
    if not bot_token:
        return Response(status_code=404)
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}")
        if not r.is_success or not r.json().get("ok"):
            return Response(status_code=404)
        file_path = r.json()["result"]["file_path"]
        img = await client.get(f"https://api.telegram.org/file/bot{bot_token}/{file_path}")
        return Response(content=img.content, media_type="image/jpeg")


@app.post("/webhook/{token}")
async def webhook(token: str, request: Request):
    if token != BOT_TOKEN or not bot_app:
        return JSONResponse({"error": "invalid"}, status_code=403)
    from telegram import Update
    update = Update.de_json(await request.json(), bot_app.bot)
    await bot_app.process_update(update)
    return JSONResponse({"ok": True})


@app.post("/admin-webhook/{token}")
async def admin_webhook(token: str, request: Request):
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
