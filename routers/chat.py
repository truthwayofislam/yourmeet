from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db, User, Message, Match
from routers.auth import get_current_user

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/chat/{other_id}", response_class=HTMLResponse)
async def chat_page(other_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user:
        return RedirectResponse("/login")

    match = db.query(Match).filter(
        ((Match.user1_id == current_user.id) & (Match.user2_id == other_id)) |
        ((Match.user1_id == other_id) & (Match.user2_id == current_user.id))
    ).first()
    if not match:
        return RedirectResponse("/matches")

    other = db.query(User).filter(User.id == other_id).first()
    messages = db.query(Message).filter(
        ((Message.sender_id == current_user.id) & (Message.receiver_id == other_id)) |
        ((Message.sender_id == other_id) & (Message.receiver_id == current_user.id))
    ).order_by(Message.sent_at).all()

    return templates.TemplateResponse("chat.html", {
        "request": request,
        "user": current_user,
        "other": other,
        "messages": messages
    })

@router.post("/chat/{other_id}/send")
async def send_message(other_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    content = body.get("content", "").strip()
    if not content:
        return JSONResponse({"error": "empty"}, status_code=400)
    msg = Message(sender_id=current_user.id, receiver_id=other_id, content=content)
    db.add(msg)
    db.commit()
    return JSONResponse({"ok": True, "msg": content})

@router.get("/chat/{other_id}/messages")
async def get_messages(other_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    messages = db.query(Message).filter(
        ((Message.sender_id == current_user.id) & (Message.receiver_id == other_id)) |
        ((Message.sender_id == other_id) & (Message.receiver_id == current_user.id))
    ).order_by(Message.sent_at).all()
    return JSONResponse([{
        "sender_id": m.sender_id,
        "content": m.content,
        "sent_at": m.sent_at.strftime("%H:%M")
    } for m in messages])
