from fastapi import APIRouter, Depends, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from templating import templates
from database import get_db, row_to_user, row_to_obj
from routers.auth import get_current_user
from storage import upload_photo_to_telegram
import shutil, uuid, json as _json, os
from datetime import date, datetime, timedelta

router = APIRouter()

MATCH_KEYS = ["id","user1_id","user2_id","matched_at"]

def check_and_reset_swipes(db, user):
    today = str(date.today())
    if getattr(user, 'swipes_reset_date', '') != today:
        limit = 10 if not getattr(user, 'is_approved', 0) else 30
        db.execute("UPDATE users SET daily_swipes=?, super_likes_left=1, swipes_reset_date=? WHERE id=?", (limit, today, user.id))
        db.commit()
        user.__dict__['daily_swipes'] = limit
        user.__dict__['super_likes_left'] = 1
        user.__dict__['swipes_reset_date'] = today

@router.post("/unmatch/{match_id}")
async def unmatch(match_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    db.execute(
        "DELETE FROM matches WHERE id=? AND (user1_id=? OR user2_id=?)",
        (match_id, current_user.id, current_user.id)
    )
    db.commit()
    return JSONResponse({"ok": True})

@router.get("/profile/stats")
async def profile_stats(db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    likes_given = db.execute("SELECT COUNT(*) FROM likes WHERE from_user=?", (current_user.id,)).fetchone()[0]
    likes_received = db.execute("SELECT COUNT(*) FROM likes WHERE to_user=?", (current_user.id,)).fetchone()[0]
    matches = db.execute("SELECT COUNT(*) FROM matches WHERE user1_id=? OR user2_id=?", (current_user.id, current_user.id)).fetchone()[0]
    return JSONResponse({"likes_given": likes_given, "likes_received": likes_received, "matches": matches})


async def setup_page(request: Request):
    return templates.TemplateResponse(request, "setup.html")

@router.post("/setup/submit")
async def setup_submit(
    request: Request,
    name: str = Form(...), age: int = Form(...), gender: str = Form(...),
    interested_in: str = Form("both"), bio: str = Form(""),
    social_handle: str = Form(""), city: str = Form(""), phone: str = Form(""),
    interests: str = Form(""), telegram_id: str = Form(None),
    photos: list[UploadFile] = File(default=[]),
    db=Depends(get_db)
):
    if age < 18:
        return JSONResponse({"error": "Age must be 18+"}, status_code=400)
    if len(bio.strip()) < 10:
        return JSONResponse({"error": "Bio must be at least 10 characters"}, status_code=400)
    if len(social_handle.strip()) < 2:
        return JSONResponse({"error": "Social handle is required"}, status_code=400)
    if not photos or not photos[0].filename:
        return JSONResponse({"error": "At least 1 photo is required"}, status_code=400)

    # Upload photos — first one is main photo, rest stored in photos JSON
    uploaded = []
    for photo in photos[:6]:
        if not photo.filename:
            continue
        path = await upload_photo_to_telegram(photo)
        if not path:
            os.makedirs("static/img", exist_ok=True)
            ext = photo.filename.split(".")[-1]
            fname = f"{uuid.uuid4()}.{ext}"
            path = f"static/img/{fname}"
            with open(path, "wb") as f:
                shutil.copyfileobj(photo.file, f)
        uploaded.append(path)

    main_photo = uploaded[0] if uploaded else ""
    photos_json = _json.dumps(uploaded)

    tg_id = telegram_id or None
    import secrets as _sec
    email = f"tg_{tg_id}@yourmeet.app" if tg_id else f"setup_{_sec.token_hex(6)}@yourmeet.app"
    password = _sec.token_hex(16)

    existing = db.execute("SELECT id, is_blocked, is_rejected FROM users WHERE telegram_id=?", (tg_id,)).fetchone() if tg_id else None
    if existing and existing[1]:  # is_blocked
        return JSONResponse({"error": "Account permanently banned"}, status_code=403)
    if existing and existing[2]:  # is_rejected — delete old, re-register
        old_id = existing[0]
        for tbl, cols in [("likes","from_user,to_user"),("matches","user1_id,user2_id"),("skips","user_id,skipped_id"),("referrals","referrer_id,referred_id"),("reports","reporter_id,reported_id")]:
            c1, c2 = cols.split(",")
            db.execute(f"DELETE FROM {tbl} WHERE {c1}=? OR {c2}=?", (old_id, old_id))
        db.execute("DELETE FROM users WHERE id=?", (old_id,))
        db.commit()
        existing = None

    if existing:
        db.execute(
            "UPDATE users SET name=?,age=?,gender=?,interested_in=?,bio=?,social_handle=?,city=?,phone=?,"
            "photo=?,photos=?,interests=?,is_approved=0 WHERE telegram_id=?",
            (name, age, gender, interested_in, bio, social_handle, city, phone or None, main_photo, photos_json, interests, tg_id)
        )
        db.commit()
        user_id = existing[0]
    else:
        db.execute(
            "INSERT INTO users (name,email,password,age,gender,interested_in,bio,social_handle,city,phone,"
            "photo,photos,interests,telegram_id,is_approved,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,datetime('now'))",
            (name, email, password, age, gender, interested_in, bio, social_handle, city, phone or None, main_photo, photos_json, interests, tg_id)
        )
        db.commit()
        user_id = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()[0]

    try:
        from admin_bot import send_for_review
        await send_for_review(user_id, name, age, gender, city, main_photo, email, "")
    except Exception as e:
        print(f"[SETUP SUBMIT] admin notify failed: {e}")

    return JSONResponse({"ok": True})


async def home(request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return RedirectResponse("/login")
    # Incomplete profile — force complete registration
    if not current_user.gender or not current_user.photo or not getattr(current_user, 'age', 0):
        return RedirectResponse("/register")
    # Not approved yet — show pending page with limited swipe access
    if not getattr(current_user, 'is_approved', 0):
        liked = [r[0] for r in db.execute("SELECT to_user FROM likes WHERE from_user=?", (current_user.id,)).fetchall()]
        skipped = [r[0] for r in db.execute("SELECT skipped_id FROM skips WHERE user_id=?", (current_user.id,)).fetchall()]
        excluded = list(set(liked + skipped + [current_user.id]))
        placeholders = ",".join("?" * len(excluded))
        rows = db.execute(
            f"""SELECT id,name,email,phone,password,age,gender,bio,city,photo,is_premium,super_likes_left,
                created_at,telegram_id,is_admin,is_blocked,daily_swipes,swipes_reset_date,referral_count,
                social_handle,is_verified,boosted_until,is_approved,premium_until,is_rejected,
                language,interested_in,photos,interests,setup_step
                FROM users WHERE id NOT IN ({placeholders}) AND gender!=? AND age>=18 AND is_blocked=0 AND is_rejected=0 AND is_approved=1 ORDER BY RANDOM() LIMIT 10""",
            (*excluded, current_user.gender or 'none')
        ).fetchall()
        profiles = [row_to_user(r) for r in rows]
        return templates.TemplateResponse(request, "pending.html", context={"user": current_user, "profiles": profiles, "age_min": 18, "age_max": 99})
    liked = [r[0] for r in db.execute("SELECT to_user FROM likes WHERE from_user=?", (current_user.id,)).fetchall()]
    skipped = [r[0] for r in db.execute("SELECT skipped_id FROM skips WHERE user_id=?", (current_user.id,)).fetchall()]
    excluded = list(set(liked + skipped + [current_user.id]))
    placeholders = ",".join("?" * len(excluded))
    interested_in = getattr(current_user, 'interested_in', 'both') or 'both'
    if interested_in == 'both':
        gender_filter = "gender IN ('male','female')"
        gender_params = ()
    else:
        gender_filter = "gender=?"
        gender_params = (interested_in,)
    age_min = max(18, min(int(request.query_params.get("age_min", 18)), 99))
    age_max = max(18, min(int(request.query_params.get("age_max", 99)), 99))
    rows = db.execute(
        f"""SELECT id,name,email,phone,password,age,gender,bio,city,photo,is_premium,super_likes_left,
            created_at,telegram_id,is_admin,is_blocked,daily_swipes,swipes_reset_date,referral_count,
            social_handle,is_verified,boosted_until,is_approved,premium_until,is_rejected,
            language,interested_in,photos,interests,setup_step
            FROM users WHERE id NOT IN ({placeholders}) AND {gender_filter} AND age BETWEEN ? AND ? AND is_blocked=0 AND is_rejected=0 AND is_approved=1
            ORDER BY CASE WHEN boosted_until > datetime('now') THEN 0 ELSE 1 END, RANDOM() LIMIT 10""",
        (*excluded, *gender_params, age_min, age_max)
    ).fetchall()
    profiles = [row_to_user(r) for r in rows]
    return templates.TemplateResponse(request, "index.html", context={"user": current_user, "profiles": profiles, "active": "home", "age_min": age_min, "age_max": age_max})

@router.post("/like/{target_id}")
async def like_user(target_id: int, request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if target_id == current_user.id:
        return JSONResponse({"error": "invalid"}, status_code=400)
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
                target_row = db.execute("SELECT telegram_id, social_handle, is_premium FROM users WHERE id=?", (target_id,)).fetchone()
                if bot_app and target_row and target_row[0]:
                    await notify_match(bot_app.bot, target_row[0], current_user.name, current_user.social_handle or "", bool(target_row[2]))
                if bot_app and current_user.telegram_id:
                    target_name_row = db.execute("SELECT name, social_handle FROM users WHERE id=?", (target_id,)).fetchone()
                    if target_name_row:
                        await notify_match(bot_app.bot, current_user.telegram_id, target_name_row[0], target_name_row[1] or "", bool(current_user.is_premium))
            except: pass
        return JSONResponse({"matched": True})
    return JSONResponse({"matched": False})

@router.post("/skip/{target_id}")
async def skip_user(target_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if target_id == current_user.id:
        return JSONResponse({"error": "invalid"}, status_code=400)
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
    if not current_user.gender or not current_user.photo:
        return RedirectResponse("/register")
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
            db.execute("UPDATE users SET is_blocked=1, is_approved=0 WHERE id=?", (target_id,))
        db.commit()
    return JSONResponse({"ok": True})

@router.get("/matches", response_class=HTMLResponse)
async def matches_page(request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return RedirectResponse("/login")
    if not current_user.gender or not current_user.photo:
        return RedirectResponse("/register")
    match_rows = db.execute(
        "SELECT id,user1_id,user2_id,matched_at FROM matches WHERE user1_id=? OR user2_id=? ORDER BY matched_at DESC", (current_user.id, current_user.id)
    ).fetchall()
    matched_users = []
    for r in match_rows:
        m = row_to_obj(r, MATCH_KEYS)
        other_id = m.user2_id if m.user1_id == current_user.id else m.user1_id
        urow = db.execute(
            "SELECT id,name,email,phone,password,age,gender,bio,city,photo,is_premium,super_likes_left,"
            "created_at,telegram_id,is_admin,is_blocked,daily_swipes,swipes_reset_date,referral_count,"
            "social_handle,is_verified,boosted_until,is_approved,premium_until,is_rejected,"
            "language,interested_in,photos,interests,setup_step "
            "FROM users WHERE id=?", (other_id,)
        ).fetchone()
        if urow:
            u = row_to_user(urow)
            u.__dict__['match_id'] = m.id
            u.__dict__['matched_at'] = (m.matched_at or '')[:10]
            matched_users.append(u)
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
    db.execute("UPDATE users SET bio=?, city=?, photo=?, social_handle=?, age=?, gender=?, is_approved=0 WHERE id=?",
               (bio, city, photo_path, social_handle, new_age, new_gender, current_user.id))
    db.commit()
    # Notify admin for re-approval
    try:
        from admin_bot import send_for_review
        await send_for_review(current_user.id, current_user.name, new_age, new_gender, city, photo_path,
                              getattr(current_user, 'email', ''), getattr(current_user, 'phone', ''))
    except: pass
    return RedirectResponse("/profile", status_code=302)
