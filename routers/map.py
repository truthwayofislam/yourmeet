from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from database import get_db, row_to_user, USER_COLS
from routers.auth import get_current_user
from routers.profiles import _reset_swipes_if_needed
from storage import photo_url
from templating import templates

router = APIRouter()

_COLS = ", ".join(USER_COLS)


@router.get("/map", response_class=HTMLResponse)
async def map_page(request: Request, current_user=Depends(get_current_user)):
    if not current_user:
        return RedirectResponse("/setup", status_code=302)
    return templates.TemplateResponse(
        request, "map.html", {"user": current_user, "active": "map"}
    )


@router.get("/api/map/match")
async def get_map_match(request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    """Return a random approved user for map discovery."""
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    liked = [r[0] for r in db.execute("SELECT to_user FROM likes WHERE from_user=?", (current_user.id,)).fetchall()]
    skipped = [r[0] for r in db.execute("SELECT skipped_id FROM skips WHERE user_id=?", (current_user.id,)).fetchall()]
    excluded = list(set(liked + skipped + [current_user.id]))
    placeholders = ",".join("?" * len(excluded))

    interested_in = getattr(current_user, "interested_in", "both") or "both"
    if interested_in == "both":
        gender_sql = "gender IN ('male','female')"
        gender_params = ()
    else:
        gender_sql = "gender=?"
        gender_params = (interested_in,)

    row = db.execute(
        f"""SELECT {_COLS} FROM users
            WHERE id NOT IN ({placeholders})
            AND {gender_sql}
            AND age >= 18
            AND is_blocked=0 AND is_rejected=0 AND is_approved=1
            AND lat != 0 AND lng != 0
            ORDER BY RANDOM()
            LIMIT 1""",
        (*excluded, *gender_params),
    ).fetchone()

    if not row:
        # Try without lat/lng filter
        row = db.execute(
            f"""SELECT {_COLS} FROM users
                WHERE id NOT IN ({placeholders})
                AND {gender_sql}
                AND age >= 18
                AND is_blocked=0 AND is_rejected=0 AND is_approved=1
                ORDER BY RANDOM()
                LIMIT 1""",
            (*excluded, *gender_params),
        ).fetchone()

    if not row:
        return JSONResponse({"error": "no_users"})

    user = row_to_user(row)
    import json
    photos = json.loads(user.photos or "[]")

    return JSONResponse({
        "id": user.id,
        "name": user.name,
        "age": user.age,
        "city": user.city or "",
        "bio": user.bio or "",
        "photo": photo_url(user.photo),
        "photos": [photo_url(p) for p in photos],
        "interests": json.loads(user.interests or "[]"),
        "is_verified": user.is_verified,
        "lat": user.lat or 20.0,
        "lng": user.lng or 0.0,
    })


@router.post("/api/map/like/{target_id}")
async def map_like(target_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    """Like from map — respects swipe limits same as swipe feed."""
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if target_id == current_user.id:
        return JSONResponse({"error": "invalid"}, status_code=400)

    if not current_user.is_premium:
        _reset_swipes_if_needed(db, current_user)
        if current_user.daily_swipes <= 0:
            return JSONResponse({"error": "limit", "limit": True})

    already = db.execute(
        "SELECT id FROM likes WHERE from_user=? AND to_user=?",
        (current_user.id, target_id),
    ).fetchone()
    if not already:
        db.execute(
            "INSERT INTO likes (from_user, to_user, is_super) VALUES (?,?,0)",
            (current_user.id, target_id),
        )
        if not current_user.is_premium:
            db.execute("UPDATE users SET daily_swipes=daily_swipes-1 WHERE id=?", (current_user.id,))
        db.commit()

    mutual = db.execute(
        "SELECT id FROM likes WHERE from_user=? AND to_user=?",
        (target_id, current_user.id),
    ).fetchone()
    if mutual:
        existing = db.execute(
            "SELECT id FROM matches WHERE (user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?)",
            (current_user.id, target_id, target_id, current_user.id),
        ).fetchone()
        if not existing:
            db.execute("INSERT INTO matches (user1_id, user2_id) VALUES (?,?)", (current_user.id, target_id))
            db.commit()
            try:
                from routers.chat import _start_chat_session
                target_user = row_to_user(db.execute(f"SELECT {_COLS} FROM users WHERE id=?", (target_id,)).fetchone())
                match_row = db.execute(
                    "SELECT id FROM matches WHERE (user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?)",
                    (current_user.id, target_id, target_id, current_user.id)
                ).fetchone()
                if match_row and target_user:
                    await _start_chat_session(db, match_row[0], current_user, target_user)
            except Exception as e:
                print(f"[MAP LIKE] chat session failed: {e}")
            try:
                from main import bot_app
                from bot import notify_match
                from routers.vibe import send_vibe_question_to_match
                target_user = row_to_user(db.execute(f"SELECT {_COLS} FROM users WHERE id=?", (target_id,)).fetchone())
                if bot_app and target_user and target_user.telegram_id:
                    await notify_match(bot_app.bot, target_user, current_user)
                if bot_app and current_user.telegram_id:
                    await notify_match(bot_app.bot, current_user, target_user)
                if bot_app and target_user and match_row:
                    await send_vibe_question_to_match(bot_app.bot, match_row[0], current_user, target_user)
            except Exception as e:
                print(f"[MAP LIKE] notify failed: {e}")
        return JSONResponse({"matched": True})

    return JSONResponse({"matched": False})
