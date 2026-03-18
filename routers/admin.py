from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from templating import templates
from database import get_db, row_to_user, row_to_obj
from routers.auth import get_current_user
import random, secrets, os

router = APIRouter()
PAY_KEYS = ["id","user_id","razorpay_order_id","razorpay_payment_id","amount","plan","status","created_at"]
SEED_SECRET = os.getenv("SEED_SECRET", "")

GIRL_NAMES = ["Priya","Anjali","Neha","Pooja","Sneha","Riya","Kavya","Divya","Shreya","Ananya","Nisha","Meera","Sonal","Tanya","Simran","Komal","Pallavi","Swati","Deepika","Isha","Ritika","Megha","Aisha","Zara","Naina","Sakshi","Mansi","Preeti","Jyoti","Rekha","Surbhi","Ankita","Bhavna","Charu","Disha","Esha","Falak","Garima","Hina","Ishita","Juhi","Kriti","Lavanya","Monika","Natasha","Payal","Roshni","Sanya","Tanvi","Uma","Vandana","Yamini","Zoya","Aditi","Bhumi","Dimple","Ekta","Gunjan","Harshita","Indu","Jasmine","Kiran","Lata","Madhuri","Namrata","Poonam","Radhika","Sunita","Taruna","Urvashi","Varsha","Archana","Bindiya","Chanchal","Deepa","Elina","Farida","Geeta","Heena","Indira","Jayanti","Kamini","Leena","Manju","Nandita","Pinky","Rani","Savita","Trishna","Chhavi","Falguni","Omna","Wasima","Yashika","Zainab","Qurrat","Ojasvi","Warda","Xena"]
BOY_NAMES = ["Rahul","Arjun","Vikram","Rohit","Amit","Karan","Nikhil","Siddharth","Aditya","Varun","Rajan","Suresh","Manish","Deepak","Gaurav","Harish","Ishan","Jayesh","Kunal","Lokesh"]
CITIES = ["Mumbai","Delhi","Bangalore","Hyderabad","Chennai","Pune","Kolkata","Jaipur","Ahmedabad","Surat","Lucknow","Chandigarh","Indore","Bhopal","Nagpur"]
GIRL_BIOS = ["Love chai and sunsets ☕🌅","Foodie at heart 🍕","Dancer & dreamer 💃","Books > people 📚","Travel addict ✈️","Fitness freak 💪","Music is my therapy 🎵","Dog mom 🐶","Coffee lover & Netflix binger","Artist by soul 🎨","Yoga & vibes 🧘","Laughing is my cardio 😂","Simple girl, big dreams ✨","Sunflower in a world of roses 🌻","Bollywood fan 🎬","Cooking is my love language 🍳","Night owl 🦉","Spreading good vibes only 🌈"]
BOY_BIOS = ["Gym & grind 💪","Cricket lover 🏏","Foodie & traveler 🌍","Music producer 🎧","Engineer by day, gamer by night 🎮","Chai addict ☕","Bike rides & sunsets 🏍️","Simple guy, big heart ❤️","Fitness & fun 🏋️","Dog dad 🐕","Traveler & photographer 📸","Startup founder 🚀","Movie buff 🎬","Coder & coffee ☕","Adventure seeker 🏔️"]
GIRL_PHOTOS = ["https://images.unsplash.com/photo-1529626455594-4ff0802cfb7e?w=400","https://images.unsplash.com/photo-1524504388940-b1c1722653e1?w=400","https://images.unsplash.com/photo-1531746020798-e6953c6e8e04?w=400","https://images.unsplash.com/photo-1488426862026-3ee34a7d66df?w=400","https://images.unsplash.com/photo-1502823403499-6ccfcf4fb453?w=400","https://images.unsplash.com/photo-1494790108377-be9c29b29330?w=400","https://images.unsplash.com/photo-1517841905240-472988babdf9?w=400","https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=400","https://images.unsplash.com/photo-1521146764736-56c929d59c83?w=400","https://images.unsplash.com/photo-1488161628813-04466f872be2?w=400"]
BOY_PHOTOS = ["https://images.unsplash.com/photo-1500648767791-00dcc994a43e?w=400","https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=400","https://images.unsplash.com/photo-1519085360753-af0119f7cbe7?w=400","https://images.unsplash.com/photo-1492562080023-ab3db95bfbce?w=400","https://images.unsplash.com/photo-1463453091185-61582044d556?w=400"]

@router.get("/admin/seed/{secret}")
async def seed_profiles(secret: str, db=Depends(get_db)):
    if not SEED_SECRET or secret != SEED_SECRET:
        return JSONResponse({"error": "unauthorized"}, status_code=403)
    count = 0
    profiles = [(n,"female",GIRL_PHOTOS,GIRL_BIOS) for n in GIRL_NAMES] + [(n,"male",BOY_PHOTOS,BOY_BIOS) for n in BOY_NAMES]
    for name, gender, photos, bios in profiles:
        email = f"fake_{name.lower()}_{secrets.token_hex(4)}@yourmeet.app"
        try:
            db.execute(
                "INSERT INTO users (name,email,password,age,gender,city,bio,photo,created_at,is_premium,super_likes_left,daily_swipes) VALUES (?,?,?,?,?,?,?,?,datetime('now'),0,3,10)",
                (name, email, secrets.token_hex(16), random.randint(18,28), gender, random.choice(CITIES), random.choice(bios), random.choice(photos))
            )
            count += 1
        except: pass
    db.commit()
    return JSONResponse({"added": count})

@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        return RedirectResponse("/login")
    total_users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    premium_users = db.execute("SELECT COUNT(*) FROM users WHERE is_premium=1").fetchone()[0]
    total_matches = db.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    revenue_row = db.execute("SELECT SUM(amount) FROM payments WHERE status='paid'").fetchone()[0]
    total_revenue = (revenue_row or 0) / 100
    recent_users = [row_to_user(r) for r in db.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT 20").fetchall()]
    recent_payments = [row_to_obj(r, PAY_KEYS) for r in db.execute("SELECT * FROM payments ORDER BY created_at DESC LIMIT 10").fetchall()]
    return templates.TemplateResponse("admin.html", {
        "request": request, "user": current_user,
        "total_users": total_users, "premium_users": premium_users,
        "total_matches": total_matches, "total_revenue": total_revenue,
        "recent_users": recent_users, "recent_payments": recent_payments,
    })

@router.post("/admin/toggle-premium/{user_id}")
async def toggle_premium(user_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        return RedirectResponse("/login")
    row = db.execute("SELECT is_premium FROM users WHERE id=?", (user_id,)).fetchone()
    if row:
        new_val = 0 if row[0] else 1
        db.execute("UPDATE users SET is_premium=?, super_likes_left=? WHERE id=?", (new_val, 999 if new_val else 3, user_id))
        db.commit()
    return RedirectResponse("/admin", status_code=302)

@router.post("/admin/toggle-block/{user_id}")
async def toggle_block(user_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        return RedirectResponse("/login")
    row = db.execute("SELECT is_blocked FROM users WHERE id=?", (user_id,)).fetchone()
    if row:
        db.execute("UPDATE users SET is_blocked=? WHERE id=?", (0 if row[0] else 1, user_id))
        db.commit()
    return RedirectResponse("/admin", status_code=302)

@router.post("/admin/delete/{user_id}")
async def delete_user(user_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        return RedirectResponse("/login")
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    return RedirectResponse("/admin", status_code=302)

@router.get("/admin/make-admin/{user_id}")
async def make_admin(user_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user or not current_user.is_admin:
        return RedirectResponse("/login")
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (user_id,))
    db.commit()
    return RedirectResponse("/admin", status_code=302)
