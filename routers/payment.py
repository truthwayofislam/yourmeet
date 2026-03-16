from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db, User, Payment
from routers.auth import get_current_user
import razorpay, hmac, hashlib, os

router = APIRouter()
templates = Jinja2Templates(directory="templates")

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "rzp_test_YOUR_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "YOUR_KEY_SECRET")

PLANS = {
    "monthly": {"amount": 29900, "label": "₹299/month", "days": 30},
    "quarterly": {"amount": 69900, "label": "₹699/3 months", "days": 90},
}

def get_razorpay_client():
    return razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

@router.get("/premium", response_class=HTMLResponse)
async def premium_page(request: Request, current_user: User = Depends(get_current_user)):
    if not current_user:
        return RedirectResponse("/login")
    return templates.TemplateResponse("premium.html", {
        "request": request,
        "user": current_user,
        "plans": PLANS,
        "razorpay_key": RAZORPAY_KEY_ID,
        "active": "premium"
    })

@router.post("/payment/create")
async def create_order(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    plan = body.get("plan", "monthly")
    if plan not in PLANS:
        return JSONResponse({"error": "invalid plan"}, status_code=400)

    client = get_razorpay_client()
    order = client.order.create({
        "amount": PLANS[plan]["amount"],
        "currency": "INR",
        "payment_capture": 1
    })

    db.add(Payment(
        user_id=current_user.id,
        razorpay_order_id=order["id"],
        amount=PLANS[plan]["amount"],
        plan=plan
    ))
    db.commit()

    return JSONResponse({
        "order_id": order["id"],
        "amount": PLANS[plan]["amount"],
        "key": RAZORPAY_KEY_ID,
        "name": current_user.name,
        "email": current_user.email,
        "phone": current_user.phone
    })

@router.post("/payment/verify")
async def verify_payment(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()

    order_id = body.get("razorpay_order_id")
    payment_id = body.get("razorpay_payment_id")
    signature = body.get("razorpay_signature")

    # Signature verify
    msg = f"{order_id}|{payment_id}"
    expected = hmac.new(RAZORPAY_KEY_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return JSONResponse({"error": "invalid signature"}, status_code=400)

    payment = db.query(Payment).filter(Payment.razorpay_order_id == order_id).first()
    if payment:
        payment.razorpay_payment_id = payment_id
        payment.status = "paid"
        current_user.is_premium = True
        current_user.super_likes_left = 999
        db.commit()

    return JSONResponse({"success": True})
