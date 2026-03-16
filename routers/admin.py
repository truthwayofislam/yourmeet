from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from database import get_db, User, Match, Payment
from routers.auth import get_current_user

router = APIRouter()
templates = Jinja2Templates(directory="templates")

def require_admin(current_user: User = Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        return None
    return current_user

@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        return RedirectResponse("/login")

    total_users = db.query(func.count(User.id)).scalar()
    premium_users = db.query(func.count(User.id)).filter(User.is_premium == True).scalar()
    total_matches = db.query(func.count(Match.id)).scalar()
    total_revenue = db.query(func.sum(Payment.amount)).filter(Payment.status == "paid").scalar() or 0
    recent_users = db.query(User).order_by(User.created_at.desc()).limit(20).all()
    recent_payments = db.query(Payment).order_by(Payment.created_at.desc()).limit(10).all()

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "user": current_user,
        "total_users": total_users,
        "premium_users": premium_users,
        "total_matches": total_matches,
        "total_revenue": total_revenue / 100,
        "recent_users": recent_users,
        "recent_payments": recent_payments,
    })

@router.post("/admin/toggle-premium/{user_id}")
async def toggle_premium(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        return RedirectResponse("/login")
    u = db.query(User).filter(User.id == user_id).first()
    if u:
        u.is_premium = not u.is_premium
        if u.is_premium:
            u.super_likes_left = 999
        db.commit()
    return RedirectResponse("/admin", status_code=302)

@router.post("/admin/toggle-block/{user_id}")
async def toggle_block(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        return RedirectResponse("/login")
    u = db.query(User).filter(User.id == user_id).first()
    if u:
        u.is_blocked = not u.is_blocked
        db.commit()
    return RedirectResponse("/admin", status_code=302)

@router.post("/admin/delete/{user_id}")
async def delete_user(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        return RedirectResponse("/login")
    u = db.query(User).filter(User.id == user_id).first()
    if u:
        db.delete(u)
        db.commit()
    return RedirectResponse("/admin", status_code=302)

@router.get("/admin/make-admin/{user_id}")
async def make_admin(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        return RedirectResponse("/login")
    u = db.query(User).filter(User.id == user_id).first()
    if u:
        u.is_admin = True
        db.commit()
    return RedirectResponse("/admin", status_code=302)
