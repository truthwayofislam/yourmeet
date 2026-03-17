from fastapi import APIRouter, Depends, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from templating import templates
from database import get_db, row_to_user, UserObj
from storage import upload_photo_to_telegram
from jose import jwt
import bcrypt, os, shutil, uuid
from datetime import datetime

router = APIRouter()
SECRET = os.getenv("SECRET_KEY", "yourmeet_secret_key_2024")

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_token(user_id: int):
    return jwt.encode({"sub": str(user_id)}, SECRET, algorithm="HS256")

def get_current_user(request: Request, db=Depends(get_db)):
    token = request.cookies.get("token")
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
        row = db.execute("SELECT * FROM users WHERE id=?", (int(payload["sub"]),)).fetchone()
        return row_to_user(row)
    except:
        return None

@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@router.post("/register")
async def register(
    request: Request,
    name: str = Form(...), email: str = Form(...), phone: str = Form(...),
    password: str = Form(...), age: int = Form(...), gender: str = Form(...),
    city: str = Form(...), bio: str = Form(""),
    photo: UploadFile = File(None), telegram_id: str = Form(None),
    db=Depends(get_db)
):
    if age < 18:
        return templates.TemplateResponse("register.html", {"request": request, "error": "Age must be 18 or above"})
    if db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
        return templates.TemplateResponse("register.html", {"request": request, "error": "Email already registered"})

    photo_path = ""
    if photo and photo.filename:
        photo_path = await upload_photo_to_telegram(photo)
        if not photo_path:
            ext = photo.filename.split(".")[-1]
            filename = f"{uuid.uuid4()}.{ext}"
            photo_path = f"static/img/{filename}"
            with open(f"static/img/{filename}", "wb") as f:
                shutil.copyfileobj(photo.file, f)

    db.execute(
        "INSERT INTO users (name,email,phone,password,age,gender,city,bio,photo,telegram_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (name, email, phone, hash_password(password), age, gender, city, bio, photo_path, telegram_id, datetime.utcnow().isoformat())
    )
    db.commit()
    user_id = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()[0]
    response = RedirectResponse("/", status_code=302)
    response.set_cookie("token", create_token(user_id))
    return response

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@router.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...), db=Depends(get_db)):
    row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    user = row_to_user(row)
    if not user or not verify_password(password, user.password):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})
    if user.is_blocked:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Your account has been blocked"})
    response = RedirectResponse("/", status_code=302)
    response.set_cookie("token", create_token(user.id))
    return response

@router.post("/auth/telegram")
async def telegram_auth(request: Request, db=Depends(get_db)):
    body = await request.json()
    tg_id = str(body.get("id", ""))
    name = body.get("first_name", "User")
    if not tg_id:
        return JSONResponse({"error": "invalid"}, status_code=400)
    row = db.execute("SELECT * FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
    user = row_to_user(row)
    if not user:
        db.execute(
            "INSERT INTO users (name,email,phone,password,age,gender,telegram_id,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (name, f"{tg_id}@telegram.local", tg_id, hash_password(tg_id+SECRET), 18, "male", tg_id, datetime.utcnow().isoformat())
        )
        db.commit()
        user_id = db.execute("SELECT id FROM users WHERE telegram_id=?", (tg_id,)).fetchone()[0]
        new_user = True
    else:
        if user.is_blocked:
            return JSONResponse({"error": "blocked"}, status_code=403)
        user_id = user.id
        new_user = not user.bio
    return JSONResponse({"token": create_token(user_id), "new_user": new_user})

@router.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("token")
    return response
