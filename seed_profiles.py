"""
Run: python seed_profiles.py
Adds 100 fake girls + 20 fake boys to the database.
Uses real Unsplash photo URLs (no upload needed).
"""
import os, secrets
from dotenv import load_dotenv
load_dotenv()

import libsql_experimental as libsql

TURSO_URL = os.getenv("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.getenv("TURSO_DATABASE_KEY", "")

def get_conn():
    if TURSO_URL and TURSO_TOKEN:
        return libsql.connect(TURSO_URL, auth_token=TURSO_TOKEN)
    return libsql.connect("yourmeet.db")

GIRL_NAMES = [
    "Priya","Anjali","Neha","Pooja","Sneha","Riya","Kavya","Divya","Shreya","Ananya",
    "Nisha","Meera","Sonal","Tanya","Simran","Komal","Pallavi","Swati","Deepika","Isha",
    "Ritika","Megha","Aisha","Zara","Naina","Sakshi","Mansi","Preeti","Jyoti","Rekha",
    "Surbhi","Ankita","Bhavna","Charu","Disha","Esha","Falak","Garima","Hina","Ishita",
    "Juhi","Kriti","Lavanya","Monika","Natasha","Ojasvi","Payal","Qurrat","Roshni","Sanya",
    "Tanvi","Uma","Vandana","Warda","Xena","Yamini","Zoya","Aditi","Bhumi","Chhavi",
    "Dimple","Ekta","Falguni","Gunjan","Harshita","Indu","Jasmine","Kiran","Lata","Madhuri",
    "Namrata","Ojaswini","Poonam","Radhika","Sunita","Taruna","Urvashi","Varsha","Wasima","Yashika",
    "Zainab","Archana","Bindiya","Chanchal","Deepa","Elina","Farida","Geeta","Heena","Indira",
    "Jayanti","Kamini","Leena","Manju","Nandita","Omna","Pinky","Rani","Savita","Trishna",
]

BOY_NAMES = [
    "Rahul","Arjun","Vikram","Rohit","Amit","Karan","Nikhil","Siddharth","Aditya","Varun",
    "Rajan","Suresh","Manish","Deepak","Gaurav","Harish","Ishan","Jayesh","Kunal","Lokesh",
]

CITIES = ["Mumbai","Delhi","Bangalore","Hyderabad","Chennai","Pune","Kolkata","Jaipur","Ahmedabad","Surat","Lucknow","Chandigarh","Indore","Bhopal","Nagpur"]

GIRL_BIOS = [
    "Love chai and sunsets ☕🌅","Foodie at heart 🍕","Dancer & dreamer 💃","Books > people 📚",
    "Travel addict ✈️","Fitness freak 💪","Music is my therapy 🎵","Dog mom 🐶",
    "Coffee lover & Netflix binger","Artist by soul 🎨","Yoga & vibes 🧘","Foodie & explorer 🌍",
    "Laughing is my cardio 😂","Simple girl, big dreams ✨","Sunflower in a world of roses 🌻",
    "Chai pe charcha ☕","Bollywood fan 🎬","Cooking is my love language 🍳","Night owl 🦉",
    "Spreading good vibes only 🌈",
]

BOY_BIOS = [
    "Gym & grind 💪","Cricket lover 🏏","Foodie & traveler 🌍","Music producer 🎧",
    "Engineer by day, gamer by night 🎮","Chai addict ☕","Bike rides & sunsets 🏍️",
    "Simple guy, big heart ❤️","Fitness & fun 🏋️","Dog dad 🐕",
    "Traveler & photographer 📸","Startup founder 🚀","Movie buff 🎬","Coder & coffee ☕","Adventure seeker 🏔️",
    "Reading & running 📚","Chef in making 🍳","Football fanatic ⚽","Musician 🎸","Just here to vibe 😎",
]

# Unsplash face photos (stable URLs)
GIRL_PHOTOS = [
    "https://images.unsplash.com/photo-1529626455594-4ff0802cfb7e?w=400",
    "https://images.unsplash.com/photo-1524504388940-b1c1722653e1?w=400",
    "https://images.unsplash.com/photo-1531746020798-e6953c6e8e04?w=400",
    "https://images.unsplash.com/photo-1488426862026-3ee34a7d66df?w=400",
    "https://images.unsplash.com/photo-1502823403499-6ccfcf4fb453?w=400",
    "https://images.unsplash.com/photo-1494790108377-be9c29b29330?w=400",
    "https://images.unsplash.com/photo-1517841905240-472988babdf9?w=400",
    "https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=400",
    "https://images.unsplash.com/photo-1506794778202-cad84cf45f1d?w=400",
    "https://images.unsplash.com/photo-1521146764736-56c929d59c83?w=400",
]

BOY_PHOTOS = [
    "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?w=400",
    "https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=400",
    "https://images.unsplash.com/photo-1519085360753-af0119f7cbe7?w=400",
    "https://images.unsplash.com/photo-1506794778202-cad84cf45f1d?w=400",
    "https://images.unsplash.com/photo-1492562080023-ab3db95bfbce?w=400",
]

import random

def seed():
    conn = get_conn()
    count = 0

    profiles = (
        [(n, "female", GIRL_PHOTOS, GIRL_BIOS) for n in GIRL_NAMES] +
        [(n, "male", BOY_PHOTOS, BOY_BIOS) for n in BOY_NAMES]
    )

    for name, gender, photos, bios in profiles:
        email = f"fake_{name.lower()}_{secrets.token_hex(4)}@yourmeet.app"
        age = random.randint(18, 28)
        city = random.choice(CITIES)
        bio = random.choice(bios)
        photo = random.choice(photos)
        password = secrets.token_hex(16)
        try:
            conn.execute(
                "INSERT INTO users (name,email,password,age,gender,city,bio,photo,created_at,is_premium,super_likes_left,daily_swipes) VALUES (?,?,?,?,?,?,?,?,datetime('now'),0,3,10)",
                (name, email, password, age, gender, city, bio, photo)
            )
            count += 1
        except Exception as e:
            print(f"Skip {name}: {e}")

    conn.commit()
    conn.close()
    print(f"✅ {count} profiles added!")

if __name__ == "__main__":
    seed()
