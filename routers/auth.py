from fastapi import APIRouter, Depends, HTTPException, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db, User
from storage import upload_photo_to_telegram
from jose import jwt
import bcrypt, os

router = APIRouter()
templates = Jinja2Templates(directory="templates")
SECRET = os.getenv("SECRET_KEY", "yourmeet_secret_key_2024")

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_token(user_id: int):
    return jwt.encode({"sub": str(user_id)}, SECRET, algorithm="HS256")

def get_current_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("token")
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
        user = db.query(User).filter(User.id == int(payload["sub"])).first()
        return user
    except:
        return None

@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@router.post("/register")
async def register(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    password: str = Form(...),
    age: int = Form(...),
    gender: str = Form(...),
    city: str = Form(...),
    bio: str = Form(""),
    photo: UploadFile = File(None),
    telegram_id: str = Form(None),
    db: Session = Depends(get_db)
):
    if age < 18:
        return templates.TemplateResponse("register.html", {"request": request, "error": "Age must be 18 or above"})
    if db.query(User).filter(User.email == email).first():
        return templates.TemplateResponse("register.html", {"request": request, "error": "Email already registered"})

    photo_path = ""
    if photo and photo.filename:
        photo_path = await upload_photo_to_telegram(photo)
        if not photo_path:
            # local fallback
            import shutil, uuid
            ext = photo.filename.split(".")[-1]
            filename = f"{uuid.uuid4()}.{ext}"
            photo_path = f"static/img/{filename}"
            with open(f"/home/kali/dating/{photo_path}", "wb") as f:
                shutil.copyfileobj(photo.file, f)

    user = User(
        name=name, email=email, phone=phone,
        password=hash_password(password),
        age=age, gender=gender, city=city, bio=bio, photo=photo_path,
        telegram_id=telegram_id
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    response = RedirectResponse("/", status_code=302)
    response.set_cookie("token", create_token(user.id))
    return response

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.password):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})
    if user.is_blocked:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Your account has been blocked"})
    response = RedirectResponse("/", status_code=302)
    response.set_cookie("token", create_token(user.id))
    return response

@router.post("/auth/telegram")
async def telegram_auth(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    tg_id = str(body.get("id", ""))
    name = body.get("first_name", "User")
    if not tg_id:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "invalid"}, status_code=400)
    user = db.query(User).filter(User.telegram_id == tg_id).first()
    if not user:
        user = User(
            name=name, email=f"{tg_id}@telegram.local",
            phone=tg_id, password=hash_password(tg_id + SECRET),
            age=18, gender="male", telegram_id=tg_id
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    if user.is_blocked:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "blocked"}, status_code=403)
    from fastapi.responses import JSONResponse
    return JSONResponse({"token": create_token(user.id), "new_user": not user.bio})

@router.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("token")
    return response
