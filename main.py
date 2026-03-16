from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
from database import init_db
from routers import auth, profiles, chat, payment, admin
from storage import get_photo_url
from dotenv import load_dotenv
import os

load_dotenv()

app = FastAPI(title="YourMeet")
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth.router)
app.include_router(profiles.router)
app.include_router(chat.router)
app.include_router(payment.router)
app.include_router(admin.router)

# Jinja2 global filter — templates mein {{ user.photo | photo_url }} use karo
templates = Jinja2Templates(directory="templates")
templates.env.filters["photo_url"] = get_photo_url

@app.on_event("startup")
def startup():
    init_db()

# Keep-alive endpoint — Render cronjob isko ping karega
@app.get("/ping")
def ping():
    return JSONResponse({"status": "ok"})
