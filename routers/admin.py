from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from templating import templates
from database import get_db, row_to_user, row_to_obj
from routers.auth import get_current_user

router = APIRouter()
PAY_KEYS = ["id","user_id","razorpay_order_id","razorpay_payment_id","amount","plan","status","created_at"]

@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        return RedirectResponse("/login")
    total_users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    premium_users = db.execute("SELECT COUNT(*) FROM users WHERE is_premium=1").fetchone()[0]
    total_matches = db.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    revenue_row = db.execute("SELECT SUM(amount) FROM payments WHERE status='paid'").fetchone()[0]
    total_revenue = (revenue_row or 0) / 100
    recent_users = [row_to_user(r) for r in db.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT 20").fetchall()]
    recent_payments = [row_to_obj(r, PAY_KEYS) for r in db.execute("SELECT * FROM payments ORDER BY created_at DESC LIMIT 10").fetchall()]
    return templates.TemplateResponse("admin.html", {
        "request": request, "user": current_user,
        "total_users": total_users, "premium_users": premium_users,
        "total_matches": total_matches, "total_revenue": total_revenue,
        "recent_users": recent_users, "recent_payments": recent_payments,
    })

@router.post("/admin/toggle-premium/{user_id}")
async def toggle_premium(user_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        return RedirectResponse("/login")
    row = db.execute("SELECT is_premium FROM users WHERE id=?", (user_id,)).fetchone()
    if row:
        new_val = 0 if row[0] else 1
        db.execute("UPDATE users SET is_premium=?, super_likes_left=? WHERE id=?", (new_val, 999 if new_val else 3, user_id))
        db.commit()
    return RedirectResponse("/admin", status_code=302)

@router.post("/admin/toggle-block/{user_id}")
async def toggle_block(user_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        return RedirectResponse("/login")
    row = db.execute("SELECT is_blocked FROM users WHERE id=?", (user_id,)).fetchone()
    if row:
        db.execute("UPDATE users SET is_blocked=? WHERE id=?", (0 if row[0] else 1, user_id))
        db.commit()
    return RedirectResponse("/admin", status_code=302)

@router.post("/admin/delete/{user_id}")
async def delete_user(user_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        return RedirectResponse("/login")
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    return RedirectResponse("/admin", status_code=302)

@router.get("/admin/make-admin/{user_id}")
async def make_admin(user_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        return RedirectResponse("/login")
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (user_id,))
    db.commit()
    return RedirectResponse("/admin", status_code=302)
