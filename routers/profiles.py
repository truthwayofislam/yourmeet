from fastapi import APIRouter, Depends, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db, User, Like, Match
from routers.auth import get_current_user
from storage import upload_photo_to_telegram, get_photo_url

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user:
        return RedirectResponse("/login")

    liked_ids = [l.to_user for l in db.query(Like).filter(Like.from_user == current_user.id).all()]
    liked_ids.append(current_user.id)

    opposite = "female" if current_user.gender == "male" else "male"
    profiles = db.query(User).filter(
        User.id.notin_(liked_ids),
        User.gender == opposite,
        User.age >= 18,
        User.is_blocked == False
    ).limit(10).all()

    return templates.TemplateResponse("index.html", {
        "request": request,
        "user": current_user,
        "profiles": profiles,
        "active": "home"
    })

@router.post("/like/{target_id}")
async def like_user(target_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    body = await request.json()
    is_super = body.get("super", False)

    if is_super:
        if current_user.super_likes_left <= 0 and not current_user.is_premium:
            return JSONResponse({"error": "No super likes left"}, status_code=400)
        if not current_user.is_premium:
            current_user.super_likes_left -= 1

    existing = db.query(Like).filter(Like.from_user == current_user.id, Like.to_user == target_id).first()
    if not existing:
        db.add(Like(from_user=current_user.id, to_user=target_id, is_super=is_super))
        db.commit()

    mutual = db.query(Like).filter(Like.from_user == target_id, Like.to_user == current_user.id).first()
    if mutual:
        existing_match = db.query(Match).filter(
            ((Match.user1_id == current_user.id) & (Match.user2_id == target_id)) |
            ((Match.user1_id == target_id) & (Match.user2_id == current_user.id))
        ).first()
        if not existing_match:
            db.add(Match(user1_id=current_user.id, user2_id=target_id))
            db.commit()
        return JSONResponse({"matched": True})

    return JSONResponse({"matched": False})

@router.get("/matches", response_class=HTMLResponse)
async def matches_page(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user:
        return RedirectResponse("/login")

    matches = db.query(Match).filter(
        (Match.user1_id == current_user.id) | (Match.user2_id == current_user.id)
    ).all()

    matched_users = []
    for m in matches:
        other_id = m.user2_id if m.user1_id == current_user.id else m.user1_id
        other = db.query(User).filter(User.id == other_id).first()
        if other:
            matched_users.append(other)

    return templates.TemplateResponse("matches.html", {
        "request": request,
        "user": current_user,
        "matches": matched_users,
        "active": "matches"
    })

@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, current_user: User = Depends(get_current_user)):
    if not current_user:
        return RedirectResponse("/login")
    return templates.TemplateResponse("profile.html", {"request": request, "user": current_user, "active": "profile"})

@router.post("/profile/update")
async def update_profile(
    request: Request,
    bio: str = Form(""),
    city: str = Form(""),
    photo: UploadFile = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not current_user:
        return RedirectResponse("/login")
    current_user.bio = bio
    current_user.city = city
    if photo and photo.filename:
        new_path = await upload_photo_to_telegram(photo)
        if not new_path:
            import shutil, uuid
            ext = photo.filename.split(".")[-1]
            filename = f"{uuid.uuid4()}.{ext}"
            new_path = f"static/img/{filename}"
            with open(f"/home/kali/dating/{new_path}", "wb") as f:
                shutil.copyfileobj(photo.file, f)
        current_user.photo = new_path
    db.commit()
    return RedirectResponse("/profile", status_code=302)
