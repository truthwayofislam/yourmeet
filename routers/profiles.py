import json
import math
import os
from datetime import date, datetime, timedelta
import httpx
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from database import get_db, row_to_user, USER_COLS, log_audit
from routers.auth import get_current_user
import ratelimit

router = APIRouter()

_COLS = ", ".join(USER_COLS)


async def _upload_profile_photo(image: bytes, content_type: str) -> tuple[str, str]:
    main_token = os.getenv("TELEGRAM_BOTS_KEY", "").strip().strip("'\"")
    admin_token = os.getenv("ADMIN_BOT_TOKEN", "").strip().strip("'\"")
    storage_chat_id = os.getenv("TELEGRAM_STORAGE_CHAT_ID", "").strip().strip("'\"")
    if not main_token or not admin_token or not storage_chat_id:
        raise ValueError("photo storage is not configured")

    async def upload(token: str) -> str:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                data={"chat_id": storage_chat_id},
                files={"photo": ("profile-photo", image, content_type)},
            )
        result = response.json()
        if not response.is_success or not result.get("ok"):
            raise RuntimeError(result.get("description", "Telegram photo upload failed"))
        photos = result.get("result", {}).get("photo", [])
        if not photos or not photos[-1].get("file_id"):
            raise RuntimeError("Telegram did not return a photo file id")
        return photos[-1]["file_id"]

    return await upload(main_token), await upload(admin_token)


async def _geocode_city(city: str) -> tuple[float, float] | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": city, "format": "json", "limit": 1},
                headers={"User-Agent": "Sakinah/1.0"},
            )
        response.raise_for_status()
        results = response.json()
        if not results:
            return None
        return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception as exc:
        print(f"[WEB PROFILE] Location lookup failed: {exc}")
        return None


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
        user_gender = getattr(user, "gender", "")
        interested_in = "female" if user_gender == "male" else "male" if user_gender == "female" else ""
        gender_sql = "gender=?" if interested_in else "1=0"
        gender_params = (interested_in,) if interested_in else ()

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
        "interested_in": "female" if u.gender == "male" else "male" if u.gender == "female" else "",
        "bio": getattr(u, "bio", ""), "city": getattr(u, "city", ""),
        "photos": json.loads(getattr(u, "photos", "[]") or "[]"),
        "interests": json.loads(getattr(u, "interests", "[]") or "[]"),
        "marital_intent": getattr(u, "marital_intent", "serious_marriage") or "serious_marriage",
        "deeni_level": getattr(u, "deeni_level", "") or "",
        "sect": getattr(u, "sect", "") or "",
        "family_values": getattr(u, "family_values", "") or "",
        "profile_views": getattr(u, "profile_views", 0),
        "is_verified": u.is_verified, "is_premium": u.is_premium,
    })


@router.post("/api/profile/setup")
async def submit_web_profile(
    request: Request,
    name: str = Form(...),
    age: int = Form(...),
    gender: str = Form(...),
    interested_in: str = Form(...),
    city: str = Form(...),
    bio: str = Form(...),
    marital_intent: str = Form(...),
    deeni_level: str = Form(""),
    sect: str = Form(""),
    family_values: str = Form(""),
    guardian_consented: bool = Form(False),
    terms_accepted: bool = Form(...),
    photo: UploadFile | None = File(None),
    db=Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not ratelimit.allow(f"profile_setup:{current_user.id}", 5, 300):
        return JSONResponse({"error": "rate_limited"}, status_code=429)

    name = name.strip()
    city = city.strip()
    bio = bio.strip()
    deeni_level = deeni_level.strip()
    sect = sect.strip()
    family_values = family_values.strip()

    errors = {}
    if not 2 <= len(name) <= 50:
        errors["name"] = "Enter a name between 2 and 50 characters."
    if not 18 <= age <= 80:
        errors["age"] = "Age must be between 18 and 80."
    if gender not in {"male", "female"}:
        errors["gender"] = "Choose a valid gender."
    expected_interest = "female" if gender == "male" else "male" if gender == "female" else ""
    if expected_interest and interested_in != expected_interest:
        errors["interested_in"] = "Marriage matches are limited to the opposite gender."
    if not 2 <= len(city) <= 100:
        errors["city"] = "Enter a city and country between 2 and 100 characters."
    if not 10 <= len(bio) <= 300:
        errors["bio"] = "Bio must be between 10 and 300 characters."
    if marital_intent not in {
        "serious_marriage", "family_building", "mutual_consideration", "open_to_guidance"
    }:
        errors["marital_intent"] = "Choose a valid marriage intention."
    if len(deeni_level) > 100 or len(sect) > 100 or len(family_values) > 200:
        errors["values"] = "Faith and family details are too long."
    if not guardian_consented:
        errors["guardian_consented"] = "Your wali or family must be aware before submitting your profile."
    if not terms_accepted:
        errors["terms_accepted"] = "Accept the terms and privacy policy to continue."
    if errors:
        return JSONResponse({"error": "validation_error", "fields": errors}, status_code=422)

    photo_id = ""
    photo_admin_id = ""
    if photo and photo.filename:
        content_type = (photo.content_type or "").lower()
        image = await photo.read(5 * 1024 * 1024 + 1)
        if len(image) > 5 * 1024 * 1024:
            return JSONResponse(
                {"error": "validation_error", "fields": {"photo": "Photo must be 5 MB or smaller."}},
                status_code=422,
            )
        signatures = {
            "image/jpeg": image.startswith(b"\xff\xd8\xff"),
            "image/png": image.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/webp": image.startswith(b"RIFF") and image[8:12] == b"WEBP",
        }
        if content_type not in signatures or not signatures[content_type]:
            return JSONResponse(
                {"error": "validation_error", "fields": {"photo": "Upload a valid JPEG, PNG, or WebP image."}},
                status_code=422,
            )
        try:
            photo_id, photo_admin_id = await _upload_profile_photo(image, content_type)
        except ValueError as exc:
            print(f"[WEB PROFILE] {exc}")
            return JSONResponse({"error": "photo_storage_unavailable"}, status_code=503)
        except Exception as exc:
            print(f"[WEB PROFILE] Telegram photo upload failed: {exc}")
            return JSONResponse({"error": "photo_upload_failed"}, status_code=503)
        finally:
            await photo.close()

    if not photo_id and not getattr(current_user, "photo", ""):
        return JSONResponse(
            {"error": "validation_error", "fields": {"photo": "Add a profile photo to submit your profile."}},
            status_code=422,
        )

    if photo_id:
        photos_json = json.dumps([photo_id])
        saved_photo_admin_id = photo_admin_id
    else:
        photos_json = getattr(current_user, "photos", "[]") or "[]"
        saved_photo_admin_id = getattr(current_user, "photo_admin", "") or ""
        photo_id = getattr(current_user, "photo", "") or ""

    lat = getattr(current_user, "lat", 0) or 0
    lng = getattr(current_user, "lng", 0) or 0
    location_resolved = bool(lat and lng)
    if city != (getattr(current_user, "city", "") or "") or not location_resolved:
        coordinates = await _geocode_city(city)
        if coordinates:
            lat, lng = coordinates
            location_resolved = True
        else:
            lat, lng = 0, 0
            location_resolved = False

    approved = 0 if current_user.is_rejected else int(bool(current_user.is_approved))
    db.execute(
        """UPDATE users SET name=?, age=?, gender=?, interested_in=?, bio=?, city=?, lat=?, lng=?,
           photo=?, photo_admin=?, photos=?, looking_for='serious_marriage',
           marital_intent=?, deeni_level=?, sect=?, family_values=?, guardian_consented=?,
           terms_accepted=1, is_approved=?, is_rejected=0 WHERE id=?""",
        (
            name, age, gender, expected_interest, bio, city, lat, lng,
            photo_id, saved_photo_admin_id, photos_json,
            marital_intent, deeni_level, sect, family_values, int(guardian_consented),
            approved, current_user.id,
        ),
    )
    db.commit()

    review_notified = False
    if not approved:
        try:
            from admin_bot import send_for_review
            review_notified = await send_for_review(
                current_user.id, name, age, gender, city, saved_photo_admin_id
            )
        except Exception as exc:
            print(f"[WEB PROFILE] Admin review notification failed: {exc}")

    return JSONResponse({
        "ok": True,
        "status": "approved" if approved else "pending_review",
        "review_notified": review_notified,
        "location_resolved": location_resolved,
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
