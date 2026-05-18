import os
import json
import httpx
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from database import get_db, get_conn
from routers.auth import get_current_user

router = APIRouter()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
MODEL = "google/gemma-4-31b-it:free"


# ── Vibe Check ────────────────────────────────────────────────────────────────

async def _fetch_question_from_ai() -> dict:
    """Ask AI to generate a short fun either/or question."""
    if not OPENROUTER_API_KEY:
        return {"question": "Night owl or early bird?", "option_a": "🦉 Night owl", "option_b": "🐦 Early bird"}
    prompt = (
        "Generate 1 fun, short either/or question for a dating app vibe check. "
        "Keep it light and interesting. Return ONLY valid JSON like this: "
        '{"question": "...", "option_a": "emoji + short text", "option_b": "emoji + short text"} '
        "No explanation. Just JSON."
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
                json={"model": MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.9},
            )
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"].strip()
                start = content.find("{")
                end = content.rfind("}") + 1
                if start != -1 and end > start:
                    return json.loads(content[start:end])
    except Exception as e:
        print(f"[VIBE] AI question failed: {e}")
    return {"question": "Beach or mountains?", "option_a": "🏖 Beach", "option_b": "⛰ Mountains"}


def _get_or_create_today_question(db) -> dict:
    """Get today's question from DB, or generate a new one."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    row = db.execute("SELECT question, option_a, option_b FROM vibe_questions WHERE date=?", (today,)).fetchone()
    if row:
        return {"question": row[0], "option_a": row[1], "option_b": row[2], "date": today}
    return None


async def ensure_today_question(db) -> dict:
    """Ensure today's question exists, generate if not."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    existing = _get_or_create_today_question(db)
    if existing:
        return existing
    q = await _fetch_question_from_ai()
    try:
        db.execute(
            "INSERT OR IGNORE INTO vibe_questions (question, option_a, option_b, date) VALUES (?,?,?,?)",
            (q["question"], q["option_a"], q["option_b"], today),
        )
        db.commit()
    except Exception:
        pass
    return {**q, "date": today}


@router.get("/api/vibe/question")
async def get_vibe_question(db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    q = await ensure_today_question(db)
    return JSONResponse(q)


@router.post("/api/vibe/answer/{match_id}")
async def submit_vibe_answer(match_id: int, request_data: dict, db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    answer = request_data.get("answer", "")
    if answer not in ("a", "b"):
        return JSONResponse({"error": "invalid_answer"}, status_code=400)

    # Verify match belongs to user
    match = db.execute(
        "SELECT user1_id, user2_id FROM matches WHERE id=? AND (user1_id=? OR user2_id=?)",
        (match_id, current_user.id, current_user.id),
    ).fetchone()
    if not match:
        return JSONResponse({"error": "match_not_found"}, status_code=404)

    # Save answer
    try:
        db.execute(
            "INSERT OR REPLACE INTO vibe_answers (match_id, user_id, answer) VALUES (?,?,?)",
            (match_id, current_user.id, answer),
        )
        db.commit()
    except Exception:
        pass

    # Check if both answered
    other_id = match[1] if match[0] == current_user.id else match[0]
    other_answer_row = db.execute(
        "SELECT answer FROM vibe_answers WHERE match_id=? AND user_id=?",
        (match_id, other_id),
    ).fetchone()

    if not other_answer_row:
        return JSONResponse({"ok": True, "waiting": True})

    # Both answered — compare
    my_answer = answer
    other_answer = other_answer_row[0]
    matched_vibe = my_answer == other_answer

    # Get question for context
    today = datetime.utcnow().strftime("%Y-%m-%d")
    q_row = db.execute("SELECT question, option_a, option_b FROM vibe_questions WHERE date=?", (today,)).fetchone()
    question = q_row[0] if q_row else ""
    option_a = q_row[1] if q_row else "A"
    option_b = q_row[2] if q_row else "B"

    my_choice = option_a if my_answer == "a" else option_b
    other_choice = option_a if other_answer == "a" else option_b

    # Notify both via bot
    try:
        from main import bot_app
        from database import row_to_user, USER_COLS
        cols = ", ".join(USER_COLS)
        other_user = row_to_user(db.execute(f"SELECT {cols} FROM users WHERE id=?", (other_id,)).fetchone())
        if bot_app and other_user:
            await _notify_vibe_result(
                bot_app.bot, current_user, other_user,
                question, my_choice, other_choice, matched_vibe
            )
            await _notify_vibe_result(
                bot_app.bot, other_user, current_user,
                question, other_choice, my_choice, matched_vibe
            )
    except Exception as e:
        print(f"[VIBE] notify failed: {e}")

    return JSONResponse({
        "ok": True,
        "waiting": False,
        "matched_vibe": matched_vibe,
        "my_choice": my_choice,
        "other_choice": other_choice,
        "question": question,
    })


async def send_vibe_question_to_match(bot, match_id: int, user1, user2):
    """Called after a match is created — send vibe check question to both users via bot."""
    db = get_conn()
    q = await ensure_today_question(db)
    BOT_TOKEN = os.getenv("TELEGRAM_BOTS_KEY", "")
    if not BOT_TOKEN:
        return
    text = (
        f"🎯 <b>Vibe Check!</b>\n\n"
        f"<b>{q['question']}</b>\n\n"
        f"Reply with your choice:\n"
        f"A — {q['option_a']}\n"
        f"B — {q['option_b']}\n\n"
        f"<i>Your match will see the result once both answer!</i>"
    )
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(q["option_a"], callback_data=f"vibe:{match_id}:a"),
        InlineKeyboardButton(q["option_b"], callback_data=f"vibe:{match_id}:b"),
    ]])
    for tg_id in [user1.telegram_id, user2.telegram_id]:
        if not tg_id:
            continue
        try:
            await bot.send_message(chat_id=tg_id, text=text, parse_mode="HTML", reply_markup=keyboard)
        except Exception as e:
            print(f"[VIBE] send question failed: {e}")


async def _notify_vibe_result(bot, user, other_user, question, my_choice, other_choice, matched):
    if not user.telegram_id:
        return
    if matched:
        text = (
            f"✨ <b>Vibe Match!</b>\n\n"
            f"You and <b>{other_user.name}</b> both chose the same!\n\n"
            f"❓ {question}\n"
            f"✅ You both: <b>{my_choice}</b>\n\n"
            f"Great minds think alike! 💕"
        )
    else:
        text = (
            f"🎭 <b>Opposites Attract!</b>\n\n"
            f"You and <b>{other_user.name}</b> chose differently!\n\n"
            f"❓ {question}\n"
            f"You: <b>{my_choice}</b>\n"
            f"{other_user.name}: <b>{other_choice}</b>\n\n"
            f"Different vibes, same spark! 💕"
        )
    try:
        await bot.send_message(chat_id=user.telegram_id, text=text, parse_mode="HTML")
    except Exception as e:
        print(f"[VIBE] result notify failed: {e}")


# ── Mystery Mode ──────────────────────────────────────────────────────────────

@router.post("/api/mystery/toggle")
async def toggle_mystery(db=Depends(get_db), current_user=Depends(get_current_user)):
    """Premium only — toggle mystery mode for 24 hours."""
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not current_user.is_premium:
        return JSONResponse({"error": "premium_required"}, status_code=403)

    now = datetime.utcnow()
    mystery_until = getattr(current_user, "mystery_until", "") or ""

    # If currently active, turn off
    if mystery_until and datetime.strptime(mystery_until, "%Y-%m-%d %H:%M:%S") > now:
        db.execute("UPDATE users SET mystery_until='' WHERE id=?", (current_user.id,))
        db.commit()
        return JSONResponse({"ok": True, "active": False})

    # Turn on for 24 hours
    until = (now + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE users SET mystery_until=? WHERE id=?", (until, current_user.id))
    db.commit()
    return JSONResponse({"ok": True, "active": True, "until": until})


@router.get("/api/mystery/status")
async def mystery_status(db=Depends(get_db), current_user=Depends(get_current_user)):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    mystery_until = getattr(current_user, "mystery_until", "") or ""
    active = False
    if mystery_until:
        try:
            active = datetime.strptime(mystery_until, "%Y-%m-%d %H:%M:%S") > datetime.utcnow()
        except Exception:
            pass
    return JSONResponse({"active": active, "until": mystery_until if active else ""})
