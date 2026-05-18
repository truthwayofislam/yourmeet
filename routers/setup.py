import os
import json
import httpx
from fastapi import APIRouter, Depends, Request, Form, UploadFile, File
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from database import get_db, row_to_user, USER_COLS
from routers.auth import get_current_user, create_token
from storage import upload_photo
from templating import templates

router = APIRouter()


@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if current_user and current_user.photo and current_user.age and current_user.gender:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "setup.html", {"user": current_user})


@router.post("/setup/submit")
async def setup_submit(
    request: Request,
    name: str = Form(...),
    age: int = Form(...),
    gender: str = Form(...),
    interested_in: str = Form("both"),
    bio: str = Form(""),
    city: str = Form(""),
    phone: str = Form(""),
    social_handle: str = Form(""),
    interests: str = Form("[]"),
    telegram_id: str = Form(None),
    photos: list[UploadFile] = File(default=[]),
    db=Depends(get_db),
):
    try:
        # Validate
        if age < 18 or age > 60:
            return JSONResponse({"error": "Age must be between 18 and 60"}, status_code=400)
        if len(bio.strip()) < 10:
            return JSONResponse({"error": "Bio must be at least 10 characters"}, status_code=400)
        if not social_handle.strip():
            return JSONResponse({"error": "Social handle is required"}, status_code=400)
        if not city.strip():
            return JSONResponse({"error": "City is required"}, status_code=400)
        valid_photos = [p for p in photos if p.filename]
        if not valid_photos:
            return JSONResponse({"error": "At least 1 photo is required"}, status_code=400)

        # Upload photos
        uploaded = []
        for photo in valid_photos[:6]:
            file_id = await upload_photo(photo)
            if file_id:
                uploaded.append(file_id)

        if not uploaded:
            return JSONResponse({"error": "Photo upload failed. Check TELEGRAM_STORAGE_CHAT_ID env var."}, status_code=500)

        main_photo = uploaded[0]
        photos_json = json.dumps(uploaded)

        # Geocode city to lat/lng
        lat, lng = await _geocode(city)

        tg_id = telegram_id.strip() if telegram_id and telegram_id.strip() else None
        cols = ", ".join(USER_COLS)

        existing = None
        if tg_id:
            row = db.execute(
                f"SELECT {cols} FROM users WHERE telegram_id=?", (tg_id,)
            ).fetchone()
            existing = row_to_user(row)

        if existing:
            if existing.is_blocked:
                return JSONResponse({"error": "Account permanently banned"}, status_code=403)
            db.execute(
                """UPDATE users SET name=?, age=?, gender=?, interested_in=?, bio=?, city=?,
                   lat=?, lng=?, phone=?, social_handle=?, photo=?, photos=?, interests=?,
                   is_approved=0, is_rejected=0 WHERE telegram_id=?""",
                (name, age, gender, interested_in, bio.strip(), city.strip(),
                 lat, lng, phone or None, social_handle.strip(),
                 main_photo, photos_json, interests, tg_id),
            )
            db.commit()
            user_id = existing.id
        else:
            db.execute(
                """INSERT INTO users
                   (name, age, gender, interested_in, bio, city, lat, lng, phone,
                    social_handle, photo, photos, interests, telegram_id, is_approved)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                (name, age, gender, interested_in, bio.strip(), city.strip(),
                 lat, lng, phone or None, social_handle.strip(),
                 main_photo, photos_json, interests, tg_id),
            )
            db.commit()
            if tg_id:
                row = db.execute("SELECT id FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
            else:
                row = db.execute("SELECT last_insert_rowid()").fetchone()
            user_id = row[0]

        token = create_token(user_id)

        # Notify admin
        try:
            from admin_bot import send_for_review
            await send_for_review(user_id, name, age, gender, city, main_photo)
        except Exception as e:
            print(f"[SETUP] admin notify failed: {e}")

        return JSONResponse({"ok": True, "token": token})

    except Exception as e:
        import traceback
        print(f"[SETUP] 500 error: {traceback.format_exc()}")
        return JSONResponse({"error": f"Server error: {str(e)}"}, status_code=500)


async def _geocode(city: str):
    """Get lat/lng from city name using OpenStreetMap Nominatim (free)."""
    if not city:
        return 0.0, 0.0
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": city, "format": "json", "limit": 1},
                headers={"User-Agent": "YourMeet/1.0"},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data:
                    return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        print(f"[GEOCODE] failed for {city}: {e}")
    return 0.0, 0.0
