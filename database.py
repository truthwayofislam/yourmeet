import libsql_experimental as libsql
import os
from datetime import datetime

TURSO_URL = os.getenv("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.getenv("TURSO_DATABASE_KEY", "")

def get_conn():
    if TURSO_URL and TURSO_TOKEN:
        return libsql.connect(TURSO_URL, auth_token=TURSO_TOKEN)
    return libsql.connect("yourmeet.db")

def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, email TEXT UNIQUE, phone TEXT UNIQUE,
            password TEXT, age INTEGER, gender TEXT,
            bio TEXT DEFAULT '', city TEXT DEFAULT '',
            photo TEXT DEFAULT '', is_premium INTEGER DEFAULT 0,
            super_likes_left INTEGER DEFAULT 3,
            created_at TEXT, telegram_id TEXT,
            is_admin INTEGER DEFAULT 0, is_blocked INTEGER DEFAULT 0,
            daily_swipes INTEGER DEFAULT 10,
            swipes_reset_date TEXT DEFAULT '',
            referral_count INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER, referred_id INTEGER, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user1_id INTEGER, user2_id INTEGER, matched_at TEXT
        );
        CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER, to_user INTEGER,
            is_super INTEGER DEFAULT 0, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER, receiver_id INTEGER,
            content TEXT, sent_at TEXT, is_read INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, razorpay_order_id TEXT,
            razorpay_payment_id TEXT, amount INTEGER,
            plan TEXT, status TEXT DEFAULT 'pending', created_at TEXT
        );
    """)
    conn.commit()
    # Add new columns to existing tables if not present
    for col, definition in [
        ("daily_swipes", "INTEGER DEFAULT 10"),
        ("swipes_reset_date", "TEXT DEFAULT ''"),
        ("referral_count", "INTEGER DEFAULT 0"),
        ("social_handle", "TEXT DEFAULT ''"),
    ]:
        try:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
            conn.commit()
        except: pass
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS referrals (id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER, referred_id INTEGER, created_at TEXT)")
        conn.commit()
    except: pass
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY AUTOINCREMENT, reporter_id INTEGER, reported_id INTEGER, reason TEXT, created_at TEXT)")
        conn.commit()
    except: pass

def get_db():
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()

def row_to_user(row):
    if not row:
        return None
    keys = ["id","name","email","phone","password","age","gender","bio","city",
            "photo","is_premium","super_likes_left","created_at","telegram_id",
            "is_admin","is_blocked","daily_swipes","swipes_reset_date","referral_count","social_handle"]
    d = dict(zip(keys, row))
    return UserObj(d)

def row_to_obj(row, keys):
    if not row:
        return None
    return DictObj(dict(zip(keys, row)))

class DictObj:
    def __init__(self, d):
        self.__dict__.update(d)

class UserObj(DictObj):
    @property
    def is_premium(self): return bool(self.__dict__.get("is_premium", 0))
    @property
    def is_admin(self): return bool(self.__dict__.get("is_admin", 0))
    @property
    def is_blocked(self): return bool(self.__dict__.get("is_blocked", 0))
