from fastapi import APIRouter, Depends, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from templating import templates
from database import get_db, row_to_user, row_to_obj
from routers.auth import get_current_user
from storage import upload_photo_to_telegram
import shutil, uuid
from datetime import date, datetime, timedelta

router = APIRouter()

MATCH_KEYS = ["id","user1_id","user2_id","matched_at"]

def check_and_reset_swipes(db, user):
    today = str(date.today())
    if getattr(user, 'swipes_reset_date', '') != today:
        db.execute("UPDATE users SET daily_swipes=3, swipes_reset_date=? WHERE id=?", (today, user.id))
        db.commit()
        user.__dict__['daily_swipes'] = 3
        user.__dict__['swipes_reset_date'] = today

@router.get("/", response_class=HTMLResponse)
async def home(request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return RedirectResponse("/login")
    liked = [r[0] for r in db.execute("SELECT to_user FROM likes WHERE from_user=?", (current_user.id,)).fetchall()]
    skipped = [r[0] for r in db.execute("SELECT skipped_id FROM skips WHERE user_id=?", (current_user.id,)).fetchall()]
    excluded = list(set(liked + skipped + [current_user.id]))
    placeholders = ",".join("?" * len(excluded))
    opposite = "female" if current_user.gender == "male" else "male"
    age_min = max(18, min(int(request.query_params.get("age_min", 18)), 99))
    age_max = max(18, min(int(request.query_params.get("age_max", 99)), 99))
    rows = db.execute(
        f"""SELECT * FROM users WHERE id NOT IN ({placeholders}) AND gender=? AND age BETWEEN ? AND ? AND is_blocked=0
            ORDER BY CASE WHEN boosted_until > datetime('now') THEN 0 ELSE 1 END, RANDOM() LIMIT 10""",
        (*excluded, opposite, age_min, age_max)
    ).fetchall()
    profiles = [row_to_user(r) for r in rows]
    return templates.TemplateResponse(request, "index.html", context={"user": current_user, "profiles": profiles, "active": "home", "age_min": age_min, "age_max": age_max})

@router.post("/like/{target_id}")
async def like_user(target_id: int, request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    is_super = body.get("super", False)
    already_liked = db.execute("SELECT id FROM likes WHERE from_user=? AND to_user=?", (current_user.id, target_id)).fetchone()
    if not already_liked:
        if not current_user.is_premium:
            check_and_reset_swipes(db, current_user)
            if current_user.daily_swipes <= 0:
                return JSONResponse({"error": "Daily limit reached!", "limit": True}, status_code=400)
            db.execute("UPDATE users SET daily_swipes=daily_swipes-1 WHERE id=?", (current_user.id,))
        if is_super and not current_user.is_premium:
            if current_user.super_likes_left <= 0:
                return JSONResponse({"error": "No super likes left"}, status_code=400)
            db.execute("UPDATE users SET super_likes_left=super_likes_left-1 WHERE id=?", (current_user.id,))
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
                    asyncio.create_task(notify_match(bot_app.bot, target_row[0], current_user.name, current_user.social_handle or ""))
            except: pass
        return JSONResponse({"matched": True})
    return JSONResponse({"matched": False})

@router.post("/skip/{target_id}")
async def skip_user(target_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        db.execute("INSERT OR IGNORE INTO skips (user_id, skipped_id) VALUES (?,?)", (current_user.id, target_id))
        db.commit()
    except: pass
    return JSONResponse({"ok": True})

@router.post("/boost")
async def boost_profile(db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not current_user.is_premium:
        return JSONResponse({"error": "Premium required"}, status_code=403)
    until = (datetime.utcnow() + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE users SET boosted_until=? WHERE id=?", (until, current_user.id))
    db.commit()
    return JSONResponse({"ok": True, "until": until})

@router.get("/liked-me", response_class=HTMLResponse)
async def liked_me_page(request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return RedirectResponse("/login")
    if not current_user.is_premium:
        return RedirectResponse("/premium")
    rows = db.execute(
        """SELECT u.id,u.name,u.email,u.phone,u.password,u.age,u.gender,u.bio,u.city,u.photo,
              u.is_premium,u.super_likes_left,u.created_at,u.telegram_id,u.is_admin,u.is_blocked,
              u.daily_swipes,u.swipes_reset_date,u.referral_count,u.social_handle,
              u.is_verified,u.boosted_until,l.is_super
           FROM users u JOIN likes l ON l.from_user=u.id
           WHERE l.to_user=? AND u.is_blocked=0 ORDER BY l.created_at DESC""",
        (current_user.id,)
    ).fetchall()
    likers = []
    for r in rows:
        u = row_to_user(r[:22])
        u.__dict__['is_super'] = bool(r[22])
        likers.append(u)
    return templates.TemplateResponse(request, "liked_me.html", context={"user": current_user, "likers": likers, "active": "liked"})

@router.post("/report/{target_id}")
async def report_user(target_id: int, request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    reason = body.get("reason", "inappropriate")
    existing = db.execute("SELECT id FROM reports WHERE reporter_id=? AND reported_id=?", (current_user.id, target_id)).fetchone()
    if not existing:
        db.execute("INSERT INTO reports (reporter_id,reported_id,reason,created_at) VALUES (?,?,?,datetime('now'))", (current_user.id, target_id, reason))
        count = db.execute("SELECT COUNT(*) FROM reports WHERE reported_id=?", (target_id,)).fetchone()[0]
        if count >= 3:
            db.execute("UPDATE users SET is_blocked=1 WHERE id=?", (target_id,))
        db.commit()
    return JSONResponse({"ok": True})

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
    return templates.TemplateResponse(request, "matches.html", context={"user": current_user, "matches": matched_users, "active": "matches"})

@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, current_user=Depends(get_current_user)):
    if not current_user:
        return RedirectResponse("/login")
    return templates.TemplateResponse(request, "profile.html", context={"user": current_user, "active": "profile"})

@router.post("/profile/update")
async def update_profile(
    request: Request, bio: str = Form(""), city: str = Form(""),
    social_handle: str = Form(""), age: int = Form(None), gender: str = Form(None),
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
    new_age = age if age and age >= 18 else current_user.age
    new_gender = gender if gender in ("male", "female") else current_user.gender
    db.execute("UPDATE users SET bio=?, city=?, photo=?, social_handle=?, age=?, gender=? WHERE id=?",
               (bio, city, photo_path, social_handle, new_age, new_gender, current_user.id))
    db.commit()
    return RedirectResponse("/profile", status_code=302)
