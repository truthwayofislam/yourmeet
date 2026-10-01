import json
import math
from datetime import date, datetime, timedelta
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from database import get_db, row_to_user, USER_COLS, log_audit
from routers.auth import get_current_user
import ratelimit

router = APIRouter()

_COLS = ", ".join(USER_COLS)


def _haversine(lat1, lng1, lat2, lng2):
    """Distance in km between two lat/lng points."""
    if not lat1 or not lng1 or not lat2 or not lng2:
        return 0.0
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _reset_swipes_if_needed(db, user):
    today = str(date.today())
    if getattr(user, "swipes_reset_date", "") != today:
        limit = 999999 if user.is_premium else 30
        try:
            db.execute(
                "UPDATE users SET daily_swipes=?, super_likes_left=1, swipes_reset_date=? WHERE id=?",
                (limit, today, user.id),
            )
            db.commit()
        except Exception as e:
            print(f"[PROFILES] reset swipes failed: {e}")
        user._data["daily_swipes"] = limit
        user._data["super_likes_left"] = 1
        user.__dict__["daily_swipes"] = limit
        user.__dict__["super_likes_left"] = 1


def _get_feed(db, user, limit=10):
    try:
        liked = [r[0] for r in db.execute("SELECT to_user FROM likes WHERE from_user=?", (user.id,)).fetchall()]
        skipped = [r[0] for r in db.execute("SELECT skipped_id FROM skips WHERE user_id=?", (user.id,)).fetchall()]
        blocked = [r[0] for r in db.execute("SELECT blocked_id FROM user_blocks WHERE blocker_id=?", (user.id,)).fetchall()]
        # Also hide profiles of users who blocked ME — they must not be swipable.
        blocked_me = [r[0] for r in db.execute("SELECT blocker_id FROM user_blocks WHERE blocked_id=?", (user.id,)).fetchall()]
        excluded = list(set(liked + skipped + blocked + blocked_me + [user.id]))
        placeholders = ",".join("?" * len(excluded))
        interested_in = getattr(user, "interested_in", "both") or "both"
        if interested_in == "both":
            gender_sql = "gender IN ('male','female')"
            gender_params = ()
        else:
            gender_sql = "gender=?"
            gender_params = (interested_in,)

        # Optional user filters
        min_age = getattr(user, "min_age", 0) or 0
        max_age = getattr(user, "max_age", 0) or 0
        max_distance = getattr(user, "max_distance", 0) or 0
        age_sql = "AND age >= 18"
        age_params = ()
        if min_age and max_age:
            age_sql = "AND age >= ? AND age <= ?"
            age_params = (min_age, max_age)
        elif min_age:
            age_sql = "AND age >= ?"
            age_params = (min_age,)
        elif max_age:
            age_sql = "AND age <= ?"
            age_params = (max_age,)

        rows = db.execute(
            f"""SELECT {_COLS} FROM users
                WHERE id NOT IN ({placeholders})
                AND {gender_sql} {age_sql}
                AND is_blocked=0 AND is_rejected=0 AND is_approved=1
                ORDER BY CASE WHEN boosted_until > datetime('now') THEN 0 ELSE 1 END, RANDOM()
                LIMIT ?""",
            (*excluded, *gender_params, *age_params, limit),
        ).fetchall()
        result = []
        for r in rows:
            u = row_to_user(r)
            if max_distance and getattr(user, "lat", 0) and getattr(u, "lat", 0):
                d = _haversine(user.lat, user.lng, u.lat, u.lng)
                if d > max_distance:
                    continue
            result.append(u)
        return result
    except Exception as e:
        print(f"[FEED] error: {e}")
        return []


@router.post("/skip/{target_id}")
async def skip_user(target_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not ratelimit.allow(f"skip:{current_user.id}", 60, 60):
        return JSONResponse({"error": "rate_limited"}, status_code=429)
    db.execute("INSERT OR IGNORE INTO skips (user_id, skipped_id) VALUES (?,?)", (current_user.id, target_id))
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/unmatch/{match_id}")
async def unmatch(match_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not ratelimit.allow(f"unmatch:{current_user.id}", 30, 60):
        return JSONResponse({"error": "rate_limited"}, status_code=429)
    db.execute(
        "DELETE FROM matches WHERE id=? AND (user1_id=? OR user2_id=?)",
        (match_id, current_user.id, current_user.id),
    )
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/block/{target_id}")
async def block_user(target_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if target_id == current_user.id:
        return JSONResponse({"error": "invalid_target"}, status_code=400)
    if not ratelimit.allow(f"block:{current_user.id}", 20, 60):
        return JSONResponse({"error": "rate_limited"}, status_code=429)
    db.execute("INSERT OR IGNORE INTO user_blocks (blocker_id, blocked_id) VALUES (?,?)", (current_user.id, target_id))
    # Remove any existing match so the blocked user is no longer reachable
    db.execute("DELETE FROM matches WHERE (user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?)", (current_user.id, target_id, target_id, current_user.id))
    db.commit()
    log_audit(str(getattr(current_user, "telegram_id", "")), "block_user", target_id)
    return JSONResponse({"ok": True})


@router.post("/unblock/{target_id}")
async def unblock_user(target_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    db.execute("DELETE FROM user_blocks WHERE blocker_id=? AND blocked_id=?", (current_user.id, target_id))
    db.commit()
    return JSONResponse({"ok": True})


@router.get("/who-liked-you")
async def who_liked_you(db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not current_user.is_premium:
        return JSONResponse({"error": "premium_required"}, status_code=403)
    rows = db.execute(
        "SELECT DISTINCT from_user FROM likes WHERE to_user=? AND from_user NOT IN "
        "(SELECT blocked_id FROM user_blocks WHERE blocker_id=?)",
        (current_user.id, current_user.id),
    ).fetchall()
    out = []
    for (uid,) in rows:
        u = row_to_user(db.execute(f"SELECT {_COLS} FROM users WHERE id=?", (uid,)).fetchone())
        if u:
            is_super_row = db.execute("SELECT is_super FROM likes WHERE from_user=? AND to_user=?", (uid, current_user.id)).fetchone()
            out.append({
                "id": u.id, "name": u.name, "age": u.age, "gender": u.gender,
                "city": getattr(u, "city", ""), "photo": getattr(u, "photo", ""),
                "is_super": bool(is_super_row[0]) if is_super_row else False,
            })
    return JSONResponse({"users": out})


@router.get("/profile/{user_id}")
async def view_profile(user_id: int, request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not ratelimit.allow(f"profile_view:{current_user.id}", 60, 60):
        return JSONResponse({"error": "rate_limited"}, status_code=429)
    blocked = db.execute("SELECT 1 FROM user_blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?)", (current_user.id, user_id, user_id, current_user.id)).fetchone()
    if blocked:
        return JSONResponse({"error": "user_blocked"}, status_code=403)
    row = db.execute(f"SELECT {_COLS} FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        return JSONResponse({"error": "not_found"}, status_code=404)
    u = row_to_user(row)
    # Only approved (visible) profiles may be fetched — banned/rejected/pending
    # profiles must not be enumerable by ID. Own profile stays viewable.
    if user_id != current_user.id and not u.is_approved:
        return JSONResponse({"error": "not_found"}, status_code=404)
    # Track profile views
    db.execute("INSERT INTO user_views (viewer_id, viewed_id) VALUES (?,?)", (current_user.id, user_id))
    db.execute("UPDATE users SET profile_views=profile_views+1 WHERE id=?", (user_id,))
    db.commit()
    return JSONResponse({
        "id": u.id, "name": u.name, "age": u.age, "gender": u.gender,
        "interested_in": getattr(u, "interested_in", "both"),
        "bio": getattr(u, "bio", ""), "city": getattr(u, "city", ""),
        "photos": json.loads(getattr(u, "photos", "[]") or "[]"),
        "interests": json.loads(getattr(u, "interests", "[]") or "[]"),
        "social_handle": getattr(u, "social_handle", ""),
        "profile_views": getattr(u, "profile_views", 0),
        "is_verified": u.is_verified, "is_premium": u.is_premium,
    })


@router.get("/filters")
async def get_filters(db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse({
        "min_age": getattr(current_user, "min_age", 0) or 0,
        "max_age": getattr(current_user, "max_age", 0) or 0,
        "max_distance": getattr(current_user, "max_distance", 0) or 0,
    })


@router.post("/filters")
async def set_filters(request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    def _clamp_int(v, lo, hi):
        try:
            return max(lo, min(hi, int(v)))
        except (TypeError, ValueError):
            return 0
    min_age = _clamp_int(body.get("min_age", 0), 0, 100)
    max_age = _clamp_int(body.get("max_age", 0), 0, 100)
    max_distance = _clamp_int(body.get("max_distance", 0), 0, 500)
    db.execute("UPDATE users SET min_age=?, max_age=?, max_distance=? WHERE id=?", (min_age, max_age, max_distance, current_user.id))
    db.commit()
    return JSONResponse({"ok": True, "min_age": min_age, "max_age": max_age, "max_distance": max_distance})


@router.post("/report/{target_id}")
async def report_user(target_id: int, request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not ratelimit.allow(f"report:{current_user.id}", 5, 60):
        return JSONResponse({"error": "rate_limited"}, status_code=429)
    try:
        body = await request.json()
    except Exception:
        body = {}
    reason = body.get("reason", "inappropriate")
    valid_reasons = ["fake_profile", "spam", "underage", "harassment", "inappropriate", "other"]
    if reason not in valid_reasons:
        reason = "inappropriate"
    already = db.execute(
        "SELECT id FROM reports WHERE reporter_id=? AND reported_id=?", (current_user.id, target_id)
    ).fetchone()
    if not already:
        try:
            db.begin()
        except Exception:
            pass
        try:
            db.execute(
                "INSERT INTO reports (reporter_id, reported_id, reason) VALUES (?,?,?)",
                (current_user.id, target_id, reason),
            )
            db.commit()
            # Count AFTER insert so the threshold check is atomic with the insert
            row = db.execute(
                "SELECT COUNT(*) FROM reports WHERE reported_id=?", (target_id,)
            ).fetchone()
            count = row[0] if row else 0
            if count >= 3:
                # Review queue, not an instant ban — coordinated fake reports
                # must not permanently ban an innocent user (brigading).
                db.execute("UPDATE users SET is_approved=0 WHERE id=?", (target_id,))
                db.commit()
            log_audit(str(getattr(current_user, "telegram_id", "")), "report_user", target_id, reason)
        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            print(f"[REPORT] failed: {e}")
    return JSONResponse({"ok": True})
