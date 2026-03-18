from fastapi import APIRouter, Depends, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from templating import templates
from database import get_db, row_to_user, row_to_obj
from routers.auth import get_current_user
from storage import upload_photo_to_telegram
import shutil, uuid
from datetime import date

router = APIRouter()

MATCH_KEYS = ["id","user1_id","user2_id","matched_at"]

def check_and_reset_swipes(db, user):
    today = str(date.today())
    if getattr(user, 'swipes_reset_date', '') != today:
        db.execute("UPDATE users SET daily_swipes=10, swipes_reset_date=? WHERE id=?", (today, user.id))
        db.commit()
        user.__dict__['daily_swipes'] = 10
        user.__dict__['swipes_reset_date'] = today

@router.get("/", response_class=HTMLResponse)
async def home(request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return RedirectResponse("/login")
    liked = [r[0] for r in db.execute("SELECT to_user FROM likes WHERE from_user=?", (current_user.id,)).fetchall()]
    liked.append(current_user.id)
    placeholders = ",".join("?" * len(liked))
    opposite = "female" if current_user.gender == "male" else "male"
    rows = db.execute(
        f"SELECT * FROM users WHERE id NOT IN ({placeholders}) AND gender=? AND age>=18 AND is_blocked=0 LIMIT 10",
        (*liked, opposite)
    ).fetchall()
    profiles = [row_to_user(r) for r in rows]
    return templates.TemplateResponse("index.html", {"request": request, "user": current_user, "profiles": profiles, "active": "home"})

@router.post("/like/{target_id}")
async def like_user(target_id: int, request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not current_user.is_premium:
        check_and_reset_swipes(db, current_user)
        if current_user.daily_swipes <= 0:
            return JSONResponse({"error": "Daily limit reached! Share with 3 friends to get 10 more swipes.", "limit": True}, status_code=400)
        db.execute("UPDATE users SET daily_swipes=daily_swipes-1 WHERE id=?", (current_user.id,))
    body = await request.json()
    is_super = body.get("super", False)
    if is_super and not current_user.is_premium:
        if current_user.super_likes_left <= 0:
            return JSONResponse({"error": "No super likes left"}, status_code=400)
        db.execute("UPDATE users SET super_likes_left=super_likes_left-1 WHERE id=?", (current_user.id,))
    if not db.execute("SELECT id FROM likes WHERE from_user=? AND to_user=?", (current_user.id, target_id)).fetchone():
        db.execute("INSERT INTO likes (from_user,to_user,is_super,created_at) VALUES (?,?,?,datetime('now'))", (current_user.id, target_id, int(is_super)))
        db.commit()
    mutual = db.execute("SELECT id FROM likes WHERE from_user=? AND to_user=?", (target_id, current_user.id)).fetchone()
    if mutual:
        existing = db.execute(
            "SELECT id FROM matches WHERE (user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?)",
            (current_user.id, target_id, target_id, current_user.id)
        ).fetchone()
        if not existing:
            db.execute("INSERT INTO matches (user1_id,user2_id,matched_at) VALUES (?,?,datetime('now'))", (current_user.id, target_id))
            db.commit()
            # Telegram match notification
            try:
                from main import bot_app
                from bot import notify_match
                import asyncio
                target_row = db.execute("SELECT telegram_id FROM users WHERE id=?", (target_id,)).fetchone()
                if bot_app and target_row and target_row[0]:
                    asyncio.create_task(notify_match(bot_app.bot, target_row[0], current_user.name))
            except: pass
        return JSONResponse({"matched": True})
    return JSONResponse({"matched": False})

@router.get("/matches", response_class=HTMLResponse)
async def matches_page(request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return RedirectResponse("/login")
    rows = db.execute(
        "SELECT * FROM matches WHERE user1_id=? OR user2_id=?", (current_user.id, current_user.id)
    ).fetchall()
    matched_users = []
    for r in rows:
        m = row_to_obj(r, MATCH_KEYS)
        other_id = m.user2_id if m.user1_id == current_user.id else m.user1_id
        urow = db.execute("SELECT * FROM users WHERE id=?", (other_id,)).fetchone()
        if urow:
            matched_users.append(row_to_user(urow))
    return templates.TemplateResponse("matches.html", {"request": request, "user": current_user, "matches": matched_users, "active": "matches"})

@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, current_user=Depends(get_current_user)):
    if not current_user:
        return RedirectResponse("/login")
    return templates.TemplateResponse("profile.html", {"request": request, "user": current_user, "active": "profile"})

@router.post("/profile/update")
async def update_profile(
    request: Request, bio: str = Form(""), city: str = Form(""),
    social_handle: str = Form(""),
    photo: UploadFile = File(None), db=Depends(get_db), current_user=Depends(get_current_user)
):
    if not current_user:
        return RedirectResponse("/login")
    photo_path = current_user.photo
    if photo and photo.filename:
        new_path = await upload_photo_to_telegram(photo)
        if not new_path:
            ext = photo.filename.split(".")[-1]
            filename = f"{uuid.uuid4()}.{ext}"
            new_path = f"static/img/{filename}"
            with open(new_path, "wb") as f:
                shutil.copyfileobj(photo.file, f)
        photo_path = new_path
    db.execute("UPDATE users SET bio=?, city=?, photo=?, social_handle=? WHERE id=?", (bio, city, photo_path, social_handle, current_user.id))
    db.commit()
    return RedirectResponse("/profile", status_code=302)
