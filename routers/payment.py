import os
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from database import get_db
from routers.auth import get_current_user

router = APIRouter()

PLANS = {
    "monthly":   {"stars": 150, "days": 30,  "title": "YourMeet Premium — 1 Month",  "desc": "Unlimited likes, super likes, chat & more for 30 days"},
    "quarterly": {"stars": 350, "days": 90,  "title": "YourMeet Premium — 3 Months", "desc": "Unlimited likes, super likes, chat & more for 90 days"},
}


@router.post("/payment/invoice/{plan}")
async def create_invoice(plan: str, request: Request, current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if plan not in PLANS:
        return JSONResponse({"error": "invalid_plan"}, status_code=400)
    p = PLANS[plan]
    try:
        from main import bot_app
        msg = await bot_app.bot.send_invoice(
            chat_id=current_user.telegram_id,
            title=p["title"],
            description=p["desc"],
            payload=f"premium:{plan}:{current_user.id}",
            currency="XTR",
            prices=[{"label": p["title"], "amount": p["stars"]}],
            provider_token="",
        )
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def handle_successful_payment(tg_id: str, payload: str, db):
    """Called from bot on successful Stars payment."""
    parts = payload.split(":")
    if len(parts) < 3 or parts[0] != "premium":
        return
    plan = parts[1]
    if plan not in PLANS:
        return
    days = PLANS[plan]["days"]
    premium_until = (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "UPDATE users SET is_premium=1, premium_until=?, super_likes_left=999999 WHERE telegram_id=?",
        (premium_until, tg_id),
    )
    db.commit()

    # Notify admin
    try:
        admin_tg_id = os.getenv("ADMIN_TG_ID", "")
        admin_bot_token = os.getenv("ADMIN_BOT_TOKEN", "") or os.getenv("TELEGRAM_BOTS_KEY", "")
        if admin_tg_id and admin_bot_token:
            row = db.execute("SELECT name, age, city FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
            name = row[0] if row else "Unknown"
            age = row[1] if row else "-"
            city = row[2] if row else "-"
            plan_title = PLANS[plan]["title"]
            stars = PLANS[plan]["stars"]
            text = (
                f"💰 <b>New Premium Purchase!</b>\n\n"
                f"👤 {name}, {age} — {city}\n"
                f"📦 Plan: {plan_title}\n"
                f"⭐ Stars: {stars}\n"
                f"📅 Active until: {premium_until[:10]}\n"
                f"🆔 Telegram ID: {tg_id}"
            )
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"https://api.telegram.org/bot{admin_bot_token}/sendMessage",
                    json={"chat_id": admin_tg_id, "text": text, "parse_mode": "HTML"},
                )
    except Exception as e:
        print(f"[PAYMENT] admin notify failed: {e}")
