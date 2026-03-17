from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from templating import templates
from database import get_db
from routers.auth import get_current_user
import razorpay, hmac, hashlib, os

router = APIRouter()

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "rzp_test_YOUR_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "YOUR_KEY_SECRET")

PLANS = {
    "monthly": {"amount": 5000, "label": "₹50/month", "days": 30},
    "quarterly": {"amount": 12000, "label": "₹120/3 months", "days": 90},
}

@router.get("/premium", response_class=HTMLResponse)
async def premium_page(request: Request, current_user=Depends(get_current_user)):
    if not current_user:
        return RedirectResponse("/login")
    return templates.TemplateResponse("premium.html", {"request": request, "user": current_user, "plans": PLANS, "razorpay_key": RAZORPAY_KEY_ID, "active": "premium"})

@router.post("/payment/create")
async def create_order(request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    plan = body.get("plan", "monthly")
    if plan not in PLANS:
        return JSONResponse({"error": "invalid plan"}, status_code=400)
    client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    order = client.order.create({"amount": PLANS[plan]["amount"], "currency": "INR", "payment_capture": 1})
    db.execute(
        "INSERT INTO payments (user_id,razorpay_order_id,amount,plan,status,created_at) VALUES (?,?,?,?,?,datetime('now'))",
        (current_user.id, order["id"], PLANS[plan]["amount"], plan, "pending")
    )
    db.commit()
    return JSONResponse({"order_id": order["id"], "amount": PLANS[plan]["amount"], "key": RAZORPAY_KEY_ID,
                         "name": current_user.name, "email": current_user.email, "phone": current_user.phone})

@router.post("/payment/verify")
async def verify_payment(request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    order_id = body.get("razorpay_order_id")
    payment_id = body.get("razorpay_payment_id")
    signature = body.get("razorpay_signature")
    msg = f"{order_id}|{payment_id}"
    expected = hmac.new(RAZORPAY_KEY_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return JSONResponse({"error": "invalid signature"}, status_code=400)
    db.execute("UPDATE payments SET razorpay_payment_id=?, status='paid' WHERE razorpay_order_id=?", (payment_id, order_id))
    db.execute("UPDATE users SET is_premium=1, super_likes_left=999 WHERE id=?", (current_user.id,))
    db.commit()
    return JSONResponse({"success": True})
