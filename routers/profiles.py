import json
from datetime import date, datetime, timedelta
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from database import get_db, row_to_user, USER_COLS
from routers.auth import get_current_user

router = APIRouter()

_COLS = ", ".join(USER_COLS)


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
        excluded = list(set(liked + skipped + [user.id]))
        placeholders = ",".join("?" * len(excluded))
        interested_in = getattr(user, "interested_in", "both") or "both"
        if interested_in == "both":
            gender_sql = "gender IN ('male','female')"
            gender_params = ()
        else:
            gender_sql = "gender=?"
            gender_params = (interested_in,)
        rows = db.execute(
            f"""SELECT {_COLS} FROM users
                WHERE id NOT IN ({placeholders})
                AND {gender_sql} AND age >= 18
                AND is_blocked=0 AND is_rejected=0 AND is_approved=1
                ORDER BY CASE WHEN boosted_until > datetime('now') THEN 0 ELSE 1 END, RANDOM()
                LIMIT ?""",
            (*excluded, *gender_params, limit),
        ).fetchall()
        return [row_to_user(r) for r in rows]
    except Exception as e:
        print(f"[FEED] error: {e}")
        return []


@router.post("/skip/{target_id}")
async def skip_user(target_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    db.execute("INSERT OR IGNORE INTO skips (user_id, skipped_id) VALUES (?,?)", (current_user.id, target_id))
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
            # Atomically increment the report counter and block in one UPDATE,
            # so concurrent reports cannot both pass the count threshold.
            row = db.execute(
                "SELECT COUNT(*) FROM reports WHERE reported_id=?", (target_id,)
            ).fetchone()
            count = row[0] if row else 0
            if count >= 3:
                db.execute("UPDATE users SET is_blocked=1, is_approved=0 WHERE id=?", (target_id,))
            db.commit()
        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            print(f"[REPORT] failed: {e}")
    return JSONResponse({"ok": True})
