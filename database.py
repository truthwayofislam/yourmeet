from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os

# Turso LibSQL — env vars se lo
TURSO_URL = os.getenv("TURSO_DATABASE_URL", "")       # libsql://your-db.turso.io
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")

if TURSO_URL and TURSO_TOKEN:
    # Turso remote
    DATABASE_URL = f"{TURSO_URL.replace('libsql://', 'sqlite+libsql://')}?authToken={TURSO_TOKEN}&secure=true"
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # Local fallback (development)
    engine = create_engine("sqlite:///./yourmeet.db", connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100))
    email = Column(String(100), unique=True, index=True)
    phone = Column(String(15), unique=True)
    password = Column(String(200))
    age = Column(Integer)
    gender = Column(String(10))
    bio = Column(Text, default="")
    city = Column(String(100), default="")
    photo = Column(String(500), default="")   # Telegram file_id ya URL store hoga
    is_premium = Column(Boolean, default=False)
    super_likes_left = Column(Integer, default=3)
    created_at = Column(DateTime, default=datetime.utcnow)
    telegram_id = Column(String(50), nullable=True)
    is_admin = Column(Boolean, default=False)
    is_blocked = Column(Boolean, default=False)

class Match(Base):
    __tablename__ = "matches"
    id = Column(Integer, primary_key=True)
    user1_id = Column(Integer, ForeignKey("users.id"))
    user2_id = Column(Integer, ForeignKey("users.id"))
    matched_at = Column(DateTime, default=datetime.utcnow)

class Like(Base):
    __tablename__ = "likes"
    id = Column(Integer, primary_key=True)
    from_user = Column(Integer, ForeignKey("users.id"))
    to_user = Column(Integer, ForeignKey("users.id"))
    is_super = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    sender_id = Column(Integer, ForeignKey("users.id"))
    receiver_id = Column(Integer, ForeignKey("users.id"))
    content = Column(Text)
    sent_at = Column(DateTime, default=datetime.utcnow)
    is_read = Column(Boolean, default=False)

class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    razorpay_order_id = Column(String(100))
    razorpay_payment_id = Column(String(100), nullable=True)
    amount = Column(Integer)
    plan = Column(String(20))
    status = Column(String(20), default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    Base.metadata.create_all(bind=engine)
