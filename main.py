from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from database import init_db
from routers import auth, profiles, chat, payment, admin
from storage import get_photo_url
from dotenv import load_dotenv
import uvicorn, os

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="YourMeet", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth.router)
app.include_router(profiles.router)
app.include_router(chat.router)
app.include_router(payment.router)
app.include_router(admin.router)

templates = Jinja2Templates(directory="templates")
templates.env.filters["photo_url"] = get_photo_url

@app.api_route("/ping", methods=["GET", "HEAD"])
def ping():
    return JSONResponse({"status": "ok"})

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
