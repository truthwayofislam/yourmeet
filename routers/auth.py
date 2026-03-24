from fastapi import APIRouter, Depends, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from templating import templates
from database import get_db, row_to_user, UserObj
from storage import upload_photo_to_telegram
from jose import jwt
import bcrypt, os, shutil, uuid
from datetime import datetime

router = APIRouter()
SECRET = os.getenv("SECRET_KEY") or "yourmeet_secret_key_2024"

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
    return templates.TemplateResponse(request, "register.html")

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
        return templates.TemplateResponse(request, "register.html", context={"error": "Age must be 18 or above"})
    # Email format
    import re
    if not re.match(r'^[^\s@]+@[^\s@]+\.[^\s@]+$', email):
        return templates.TemplateResponse(request, "register.html", context={"error": "Enter a valid email address"})
    # Phone — min 10 digits
    digits = re.sub(r'\D', '', phone)
    if len(digits) < 10:
        return templates.TemplateResponse(request, "register.html", context={"error": "Phone must be at least 10 digits"})
    # City required
    if not city or len(city.strip()) < 2:
        return templates.TemplateResponse(request, "register.html", context={"error": "Please enter your city"})
    # Bio required
    if not bio or len(bio.strip()) < 10:
        return templates.TemplateResponse(request, "register.html", context={"error": "Bio must be at least 10 characters"})
    # Photo required
    if not photo or not photo.filename:
        return templates.TemplateResponse(request, "register.html", context={"error": "Profile photo is required"})
    if db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
        return templates.TemplateResponse(request, "register.html", context={"error": "Email already registered"})
    if phone and db.execute("SELECT id FROM users WHERE phone=?", (phone,)).fetchone():
        return templates.TemplateResponse(request, "register.html", context={"error": "Phone number already registered"})

    photo_path = ""
    if photo and photo.filename:
        photo_path = await upload_photo_to_telegram(photo)
        if not photo_path:
            os.makedirs("static/img", exist_ok=True)
            ext = photo.filename.split(".")[-1]
            filename = f"{uuid.uuid4()}.{ext}"
            photo_path = f"static/img/{filename}"
            with open(f"static/img/{filename}", "wb") as f:
                shutil.copyfileobj(photo.file, f)

    db.execute(
        "INSERT INTO users (name,email,phone,password,age,gender,city,bio,photo,telegram_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (name, email, phone, hash_password(password), age, gender, city, bio, photo_path, telegram_id or None, datetime.utcnow().isoformat())
    )
    db.commit()
    user_id = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()[0]
    print(f"[REGISTER] user_id={user_id} telegram_id={telegram_id!r}")
    # Notify admin bot
    try:
        from admin_bot import send_for_review
        await send_for_review(user_id, name, age, gender, city, photo_path, email, phone)
    except Exception as e:
        print(f"[ADMIN NOTIFY] {e}")
    response = RedirectResponse("/", status_code=302)
    response.set_cookie("token", create_token(user_id))
    return response

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")

@router.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...), db=Depends(get_db)):
    row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    user = row_to_user(row)
    if not user or not verify_password(password, user.password):
        return templates.TemplateResponse(request, "login.html", context={"error": "Invalid credentials"})
    if user.is_blocked:
        return templates.TemplateResponse(request, "login.html", context={"error": "Your account has been blocked"})
    response = RedirectResponse("/", status_code=302)
    response.set_cookie("token", create_token(user.id))
    return response

@router.post("/auth/telegram")
async def telegram_auth(request: Request, db=Depends(get_db)):
    body = await request.json()
    tg_id = str(body.get("id", ""))
    name = body.get("first_name", "User")
    # Verify Telegram hash
    import hmac, hashlib
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(body.items()) if k != "hash")
    expected = hmac.new(hashlib.sha256(os.getenv("TELEGRAM_BOTS_KEY","").encode()).digest(), data_check.encode(), hashlib.sha256).hexdigest()
    if body.get("hash") and expected != body.get("hash"):
        return JSONResponse({"error": "invalid"}, status_code=403)
    if not tg_id:
        return JSONResponse({"error": "invalid"}, status_code=400)
    row = db.execute("SELECT * FROM users WHERE telegram_id=?", (tg_id,)).fetchone()
    user = row_to_user(row)
    if user and user.is_blocked:
        # Delete old blocked account so user can re-register fresh
        old_id = user.id
        db.execute("DELETE FROM likes WHERE from_user=? OR to_user=?", (old_id, old_id))
        db.execute("DELETE FROM matches WHERE user1_id=? OR user2_id=?", (old_id, old_id))
        db.execute("DELETE FROM skips WHERE user_id=? OR skipped_id=?", (old_id, old_id))
        db.execute("DELETE FROM users WHERE id=?", (old_id,))
        db.commit()
        user = None
    if not user:
        db.execute(
            "INSERT INTO users (name,email,phone,password,age,gender,telegram_id,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (name, f"{tg_id}@telegram.local", tg_id, hash_password(tg_id+SECRET), 18, "", tg_id, datetime.utcnow().isoformat())
        )
        db.commit()
        user_id = db.execute("SELECT id FROM users WHERE telegram_id=?", (tg_id,)).fetchone()[0]
        new_user = True
        try:
            from admin_bot import send_for_review
            await send_for_review(user_id, name, 0, "", "", "", f"{tg_id}@telegram.local", tg_id)
        except Exception as e:
            print(f"[ADMIN NOTIFY] {e}")
    else:
        user_id = user.id
        new_user = not user.gender or not user.photo or not user.age
    return JSONResponse({"token": create_token(user_id), "new_user": new_user})

@router.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("token")
    return response
