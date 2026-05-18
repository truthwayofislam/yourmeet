import json
from datetime import date, datetime, timedelta
from fastapi import APIRouter, Depends, Request, Form, UploadFile, File
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from database import get_db, row_to_user, USER_COLS
from routers.auth import get_current_user
from storage import upload_photo
from templating import templates

router = APIRouter()

_COLS = ", ".join(USER_COLS)


def _reset_swipes_if_needed(db, user):
    today = str(date.today())
    if getattr(user, "swipes_reset_date", "") != today:
        limit = 30 if user.is_approved else 10
        if user.is_premium:
            limit = 999999
        db.execute(
            "UPDATE users SET daily_swipes=?, super_likes_left=1, swipes_reset_date=? WHERE id=?",
            (limit, today, user.id),
        )
        db.commit()
        user.__dict__["daily_swipes"] = limit
        user.__dict__["super_likes_left"] = 1
        user.__dict__["swipes_reset_date"] = today


# ── Pages ────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def home(request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return RedirectResponse("/setup")
    if not current_user.photo or not current_user.age or not current_user.gender:
        return RedirectResponse("/setup")
    if not current_user.is_approved:
        profiles = _get_feed(db, current_user, limit=10)
        return templates.TemplateResponse(
            request, "pending.html", {"user": current_user, "profiles": profiles}
        )
    _reset_swipes_if_needed(db, current_user)
    profiles = _get_feed(db, current_user, limit=10)
    return templates.TemplateResponse(
        request, "index.html",
        {"user": current_user, "profiles": profiles, "active": "home"},
    )


@router.get("/matches", response_class=HTMLResponse)
async def matches_page(request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return RedirectResponse("/setup")
    rows = db.execute(
        "SELECT id, user1_id, user2_id, matched_at FROM matches WHERE user1_id=? OR user2_id=? ORDER BY matched_at DESC",
        (current_user.id, current_user.id),
    ).fetchall()
    matched = []
    for match_id, u1, u2, matched_at in rows:
        other_id = u2 if u1 == current_user.id else u1
        urow = db.execute(f"SELECT {_COLS} FROM users WHERE id=?", (other_id,)).fetchone()
        if urow:
            u = row_to_user(urow)
            u.__dict__["match_id"] = match_id
            u.__dict__["matched_at"] = (matched_at or "")[:10]
            matched.append(u)
    return templates.TemplateResponse(
        request, "matches.html",
        {"user": current_user, "matches": matched, "active": "matches"},
    )


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return RedirectResponse("/setup")
    likes_given = db.execute("SELECT COUNT(*) FROM likes WHERE from_user=?", (current_user.id,)).fetchone()[0]
    likes_received = db.execute("SELECT COUNT(*) FROM likes WHERE to_user=?", (current_user.id,)).fetchone()[0]
    match_count = db.execute(
        "SELECT COUNT(*) FROM matches WHERE user1_id=? OR user2_id=?",
        (current_user.id, current_user.id),
    ).fetchone()[0]
    return templates.TemplateResponse(
        request, "profile.html",
        {
            "user": current_user,
            "active": "profile",
            "likes_given": likes_given,
            "likes_received": likes_received,
            "match_count": match_count,
        },
    )


# ── API ──────────────────────────────────────────────────────────────────────

@router.post("/like/{target_id}")
async def like_user(target_id: int, request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if target_id == current_user.id:
        return JSONResponse({"error": "invalid"}, status_code=400)

    body = await request.json()
    is_super = bool(body.get("super", False))

    # Check swipe limit
    if not current_user.is_premium:
        _reset_swipes_if_needed(db, current_user)
        if current_user.daily_swipes <= 0:
            return JSONResponse({"error": "limit", "limit": True}, status_code=200)
        if is_super and current_user.super_likes_left <= 0:
            return JSONResponse({"error": "no_super"}, status_code=200)

    # Record like
    already = db.execute(
        "SELECT id FROM likes WHERE from_user=? AND to_user=?",
        (current_user.id, target_id),
    ).fetchone()
    if not already:
        db.execute(
            "INSERT INTO likes (from_user, to_user, is_super) VALUES (?,?,?)",
            (current_user.id, target_id, int(is_super)),
        )
        if not current_user.is_premium:
            db.execute("UPDATE users SET daily_swipes=daily_swipes-1 WHERE id=?", (current_user.id,))
            if is_super:
                db.execute("UPDATE users SET super_likes_left=super_likes_left-1 WHERE id=?", (current_user.id,))
        db.commit()

    # Check mutual
    mutual = db.execute(
        "SELECT id FROM likes WHERE from_user=? AND to_user=?",
        (target_id, current_user.id),
    ).fetchone()
    if mutual:
        existing_match = db.execute(
            "SELECT id FROM matches WHERE (user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?)",
            (current_user.id, target_id, target_id, current_user.id),
        ).fetchone()
        if not existing_match:
            db.execute(
                "INSERT INTO matches (user1_id, user2_id) VALUES (?,?)",
                (current_user.id, target_id),
            )
            db.commit()
            # Auto start chat session on match
            try:
                from routers.chat import _start_chat_session
                target = row_to_user(db.execute(f"SELECT {_COLS} FROM users WHERE id=?", (target_id,)).fetchone())
                match_row = db.execute(
                    "SELECT id FROM matches WHERE (user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?)",
                    (current_user.id, target_id, target_id, current_user.id)
                ).fetchone()
                if match_row and target:
                    await _start_chat_session(db, match_row[0], current_user, target)
            except Exception as e:
                print(f"[LIKE] chat session failed: {e}")
            # Notify via bot + send vibe check
            try:
                from main import bot_app
                from bot import notify_match
                from routers.vibe import send_vibe_question_to_match
                if not target:
                    target = row_to_user(db.execute(f"SELECT {_COLS} FROM users WHERE id=?", (target_id,)).fetchone())
                if bot_app and target and target.telegram_id:
                    await notify_match(bot_app.bot, target, current_user)
                if bot_app and current_user.telegram_id:
                    await notify_match(bot_app.bot, current_user, target)
                if bot_app and target and match_row:
                    await send_vibe_question_to_match(bot_app.bot, match_row[0], current_user, target)
            except Exception as e:
                print(f"[LIKE] notify failed: {e}")
        return JSONResponse({"matched": True})

    return JSONResponse({"matched": False})


@router.post("/skip/{target_id}")
async def skip_user(target_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    db.execute(
        "INSERT OR IGNORE INTO skips (user_id, skipped_id) VALUES (?,?)",
        (current_user.id, target_id),
    )
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/unmatch/{match_id}")
async def unmatch(match_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    db.execute(
        "DELETE FROM matches WHERE id=? AND (user1_id=? OR user2_id=?)",
        (match_id, current_user.id, current_user.id),
    )
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/report/{target_id}")
async def report_user(target_id: int, request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    reason = body.get("reason", "inappropriate")
    already = db.execute(
        "SELECT id FROM reports WHERE reporter_id=? AND reported_id=?",
        (current_user.id, target_id),
    ).fetchone()
    if not already:
        db.execute(
            "INSERT INTO reports (reporter_id, reported_id, reason) VALUES (?,?,?)",
            (current_user.id, target_id, reason),
        )
        count = db.execute("SELECT COUNT(*) FROM reports WHERE reported_id=?", (target_id,)).fetchone()[0]
        if count >= 3:
            db.execute("UPDATE users SET is_blocked=1, is_approved=0 WHERE id=?", (target_id,))
        db.commit()
    return JSONResponse({"ok": True})


@router.get("/api/likes/received")
async def likes_received(db=Depends(get_db), current_user=Depends(get_current_user)):
    """Premium only — return list of users who liked current user."""
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not current_user.is_premium:
        return JSONResponse({"error": "premium_required"}, status_code=403)
    from storage import photo_url as _photo_url
    rows = db.execute(
        f"""SELECT {_COLS} FROM users
            INNER JOIN likes ON likes.from_user = users.id
            WHERE likes.to_user = ?
            AND users.is_blocked = 0 AND users.is_approved = 1
            ORDER BY likes.created_at DESC""",
        (current_user.id,),
    ).fetchall()
    result = []
    for r in rows:
        u = row_to_user(r)
        is_super_row = db.execute(
            "SELECT is_super FROM likes WHERE from_user=? AND to_user=?",
            (u.id, current_user.id)
        ).fetchone()
        result.append({
            "id": u.id,
            "name": u.name,
            "age": u.age,
            "city": u.city or "",
            "photo": _photo_url(u.photo),
            "is_verified": u.is_verified,
            "is_super": bool(is_super_row[0]) if is_super_row else False,
        })
    return JSONResponse({"likes": result})


@router.post("/boost")
async def boost(db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not current_user.is_premium:
        return JSONResponse({"error": "premium_required"}, status_code=403)
    until = (datetime.utcnow() + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE users SET boosted_until=? WHERE id=?", (until, current_user.id))
    db.commit()
    return JSONResponse({"ok": True, "until": until})


@router.post("/profile/update")
async def update_profile(
    request: Request,
    bio: str = Form(""),
    city: str = Form(""),
    social_handle: str = Form(""),
    age: int = Form(None),
    gender: str = Form(None),
    interested_in: str = Form(None),
    photo: UploadFile = File(None),
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    photo_path = current_user.photo
    if photo and photo.filename:
        new_id = await upload_photo(photo)
        if new_id:
            photo_path = new_id

    new_age = age if age and 18 <= age <= 100 else current_user.age
    new_gender = gender if gender in ("male", "female") else current_user.gender
    new_interested = interested_in if interested_in in ("male", "female", "both") else current_user.interested_in

    # Geocode if city changed
    lat, lng = current_user.lat, current_user.lng
    if city and city != current_user.city:
        from routers.setup import _geocode
        lat, lng = await _geocode(city)

    db.execute(
        """UPDATE users SET bio=?, city=?, lat=?, lng=?, social_handle=?, age=?, gender=?,
           interested_in=?, photo=?, is_approved=0, daily_swipes=10, swipes_reset_date='' WHERE id=?""",
        (bio, city, lat, lng, social_handle, new_age, new_gender, new_interested, photo_path, current_user.id),
    )
    db.commit()

    try:
        from admin_bot import send_for_review
        await send_for_review(current_user.id, current_user.name, new_age, new_gender, city, photo_path)
    except Exception:
        pass

    return JSONResponse({"ok": True})


@router.get("/api/feed")
async def get_feed(db=Depends(get_db), current_user=Depends(get_current_user)):
    """Return next batch of profiles as JSON for infinite scroll."""
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    _reset_swipes_if_needed(db, current_user)
    profiles = _get_feed(db, current_user, limit=10)
    from storage import photo_url
    result = []
    for p in profiles:
        photos = json.loads(p.photos or "[]")
        interests = json.loads(p.interests or "[]")
        # Mystery mode check
        mystery_until = getattr(p, "mystery_until", "") or ""
        is_mystery = False
        if mystery_until:
            try:
                is_mystery = datetime.strptime(mystery_until, "%Y-%m-%d %H:%M:%S") > datetime.utcnow()
            except Exception:
                pass
        result.append({
            "id": p.id,
            "name": p.name,
            "age": p.age,
            "city": p.city or "",
            "bio": p.bio or "",
            "photo": photo_url(p.photo) if not is_mystery else "",
            "photos": [photo_url(ph) for ph in photos] if not is_mystery else [],
            "interests": interests,
            "is_verified": p.is_verified,
            "is_mystery": is_mystery,
        })
    return JSONResponse({"profiles": result})


# ── Helper ───────────────────────────────────────────────────────────────────

def _get_feed(db, user, limit=10):
    liked = [r[0] for r in db.execute("SELECT to_user FROM likes WHERE from_user=?", (user.id,)).fetchall()]
    skipped = [r[0] for r in db.execute("SELECT skipped_id FROM skips WHERE user_id=?", (user.id,)).fetchall()]
    excluded = list(set(liked + skipped + [user.id]))
    placeholders = ",".join("?" * len(excluded))

    interested_in = getattr(user, "interested_in", "both") or "both"
    if interested_in == "both":
        gender_sql = "gender IN ('male','female')"
        gender_params = ()
    else:
        gender_sql = "gender=?"
        gender_params = (interested_in,)

    # Pending users get feed too (approved profiles only, limit 10)
    rows = db.execute(
        f"""SELECT {_COLS} FROM users
            WHERE id NOT IN ({placeholders})
            AND {gender_sql}
            AND age >= 18
            AND is_blocked=0 AND is_rejected=0 AND is_approved=1
            ORDER BY CASE WHEN boosted_until > datetime('now') THEN 0 ELSE 1 END, RANDOM()
            LIMIT ?""",
        (*excluded, *gender_params, limit),
    ).fetchall()
    return [row_to_user(r) for r in rows]
