from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from templating import templates
from database import get_db
from routers.auth import get_current_user

router = APIRouter()

PLANS = {
    "monthly":   {"stars": 150, "label": "150 ⭐", "days": 30},
    "quarterly": {"stars": 350, "label": "350 ⭐", "days": 90},
}

@router.get("/premium", response_class=HTMLResponse)
async def premium_page(request: Request, current_user=Depends(get_current_user)):
    if not current_user:
        return RedirectResponse("/login")
    return templates.TemplateResponse(request, "premium.html", context={"user": current_user, "active": "premium"})

@router.post("/payment/stars/invoice")
async def create_stars_invoice(request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not current_user.telegram_id:
        return JSONResponse({"error": "Telegram account required for Stars payment"}, status_code=400)
    body = await request.json()
    plan = body.get("plan", "monthly")
    if plan not in PLANS:
        return JSONResponse({"error": "invalid plan"}, status_code=400)
    try:
        from main import bot_app
        from bot import send_stars_invoice
        await send_stars_invoice(bot_app.bot, current_user.telegram_id, plan)
        return JSONResponse({"ok": True})
    except Exception as e:
        print(f"[STARS] Invoice error: {e}")
        return JSONResponse({"error": "Failed to send invoice"}, status_code=500)

@router.post("/payment/stars/activate")
async def activate_stars(request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    """Called after successful Telegram Stars payment from bot."""
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    plan = body.get("plan", "monthly")
    if plan not in PLANS:
        return JSONResponse({"error": "invalid plan"}, status_code=400)
    db.execute(
        "INSERT INTO payments (user_id, amount, plan, status, created_at) VALUES (?,?,?,'paid',datetime('now'))",
        (current_user.id, PLANS[plan]["stars"], plan)
    )
    from datetime import datetime, timedelta
    days = PLANS[plan]["days"]
    premium_until = (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE users SET is_premium=1, super_likes_left=999, premium_until=? WHERE id=?", (premium_until, current_user.id))
    db.commit()
    return JSONResponse({"success": True})
