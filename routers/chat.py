from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from templating import templates
from database import get_db, row_to_user, row_to_obj
from routers.auth import get_current_user

router = APIRouter()
MSG_KEYS = ["id","sender_id","receiver_id","content","sent_at","is_read"]

@router.get("/chat/{other_id}", response_class=HTMLResponse)
async def chat_page(other_id: int, request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return RedirectResponse("/login")
    match = db.execute(
        "SELECT id FROM matches WHERE (user1_id=? AND user2_id=?) OR (user1_id=? AND user2_id=?)",
        (current_user.id, other_id, other_id, current_user.id)
    ).fetchone()
    if not match:
        return RedirectResponse("/matches")
    other = row_to_user(db.execute("SELECT * FROM users WHERE id=?", (other_id,)).fetchone())
    rows = db.execute(
        "SELECT * FROM messages WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?) ORDER BY sent_at",
        (current_user.id, other_id, other_id, current_user.id)
    ).fetchall()
    messages = [row_to_obj(r, MSG_KEYS) for r in rows]
    return templates.TemplateResponse("chat.html", {"request": request, "user": current_user, "other": other, "messages": messages})

@router.post("/chat/{other_id}/send")
async def send_message(other_id: int, request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    content = body.get("content", "").strip()
    if not content:
        return JSONResponse({"error": "empty"}, status_code=400)
    db.execute("INSERT INTO messages (sender_id,receiver_id,content,sent_at) VALUES (?,?,?,datetime('now'))", (current_user.id, other_id, content))
    db.commit()
    # Telegram message notification
    try:
        from main import bot_app
        from bot import notify_message
        import asyncio
        other_row = db.execute("SELECT telegram_id FROM users WHERE id=?", (other_id,)).fetchone()
        if bot_app and other_row and other_row[0]:
            asyncio.create_task(notify_message(bot_app.bot, other_row[0], current_user.name))
    except: pass
    return JSONResponse({"ok": True})

@router.get("/chat/{other_id}/messages")
async def get_messages(other_id: int, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    rows = db.execute(
        "SELECT * FROM messages WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?) ORDER BY sent_at",
        (current_user.id, other_id, other_id, current_user.id)
    ).fetchall()
    msgs = [row_to_obj(r, ["id","sender_id","receiver_id","content","sent_at","is_read"]) for r in rows]
    return JSONResponse([{"sender_id": m.sender_id, "content": m.content, "sent_at": m.sent_at[-5:]} for m in msgs])
