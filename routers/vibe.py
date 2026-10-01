import os
import json
import httpx
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from database import get_db, get_conn
from routers.auth import get_current_user
from textsafe import esc
import ratelimit

router = APIRouter()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
# llama-3.3-70b-versatile was deprecated/removed by Groq — every request 404s
# and vibe questions silently fall back to the two hardcoded defaults.
MODEL = "openai/gpt-oss-120b"


def _parse_dt(value) -> datetime | None:
    """Parse a 'YYYY-MM-DD HH:MM:SS' timestamp, returning None on any bad input."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


# ── Vibe Check ────────────────────────────────────────────────────────────────

async def _fetch_question_from_ai() -> dict:
    """Ask AI to generate a short fun either/or question."""
    if not GROQ_API_KEY:
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
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
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


class VibeAnswerBody(BaseModel):
    answer: str

@router.post("/api/vibe/answer/{match_id}")
async def submit_vibe_answer_route(match_id: int, body: VibeAnswerBody, db=Depends(get_db), current_user=Depends(get_current_user)):
    return await _process_vibe_answer(match_id, body.answer, db, current_user)


async def submit_vibe_answer(match_id: int, request_data: dict, db, current_user):
    """Called directly from bot — not an HTTP route."""
    answer = request_data.get("answer", "")
    return await _process_vibe_answer(match_id, answer, db, current_user)


async def _process_vibe_answer(match_id: int, answer: str, db, current_user):
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if answer not in ("a", "b"):
        return JSONResponse({"error": "invalid_answer"}, status_code=400)

    # Verify match belongs to user
    match = db.execute(
        "SELECT user1_id, user2_id FROM matches WHERE id=? AND (user1_id=? OR user2_id=?)",
        (match_id, current_user.id, current_user.id),
    ).fetchone()
    if not match:
        return JSONResponse({"error": "match_not_found"}, status_code=404)

    if not ratelimit.allow(f"vibe:{current_user.id}", 10, 60):
        return JSONResponse({"error": "rate_limited"}, status_code=429)

    today = datetime.utcnow().strftime("%Y-%m-%d")

    # One answer per user per DAY: the date check keeps the spam guard while
    # letting users answer each new day's question (a bare user_id check would
    # block them forever after day one).
    already = db.execute(
        "SELECT answer FROM vibe_answers WHERE match_id=? AND user_id=? AND date=?",
        (match_id, current_user.id, today),
    ).fetchone()
    if already:
        return JSONResponse({"ok": True, "already_answered": True})

    # Save answer (stamped with today's date so comparisons stay same-day)
    try:
        db.execute(
            "INSERT OR REPLACE INTO vibe_answers (match_id, user_id, answer, date) VALUES (?,?,?,?)",
            (match_id, current_user.id, answer, today),
        )
        db.commit()
    except Exception:
        pass

    # Both must have answered TODAY — a stale answer from a previous day must
    # never be compared against today's question.
    other_id = match[1] if match[0] == current_user.id else match[0]
    other_answer_row = db.execute(
        "SELECT answer FROM vibe_answers WHERE match_id=? AND user_id=? AND date=?",
        (match_id, other_id, today),
    ).fetchone()

    if not other_answer_row:
        return JSONResponse({"ok": True, "waiting": True})

    # Both answered — compare
    my_answer = answer
    other_answer = other_answer_row[0]
    matched_vibe = my_answer == other_answer

    # Running compatibility score for this pair — exactly one comparison per
    # day per pair (both-answered-today is the only path here).
    try:
        score_row = db.execute(
            "SELECT asked, matched FROM vibe_scores WHERE match_id=?", (match_id,)
        ).fetchone()
        if score_row:
            db.execute(
                "UPDATE vibe_scores SET asked=asked+1, matched=matched+? WHERE match_id=?",
                (1 if matched_vibe else 0, match_id),
            )
            asked, matched = score_row[0] + 1, score_row[1] + (1 if matched_vibe else 0)
        else:
            db.execute(
                "INSERT INTO vibe_scores (match_id, asked, matched) VALUES (?,?,?)",
                (match_id, 1, 1 if matched_vibe else 0),
            )
            asked, matched = 1, 1 if matched_vibe else 0
        db.commit()
    except Exception as e:
        print(f"[VIBE] score update failed: {e}")
        asked, matched = 0, 0

    percent = round(100 * matched / asked) if asked else None
    compat_text = ""
    if percent is not None:
        compat_text = (
            f"\n\n💞 <b>Vibe compatibility: {percent}%</b> "
            f"({matched} of {asked} questions matched)"
        )

    # Get question for context
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
                question, my_choice, other_choice, matched_vibe, compat_text
            )
            await _notify_vibe_result(
                bot_app.bot, other_user, current_user,
                question, other_choice, my_choice, matched_vibe, compat_text
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
        "compatibility": {
            "asked": asked,
            "matched": matched,
            "percent": percent,
        },
    })


async def send_vibe_question_to_match(bot, match_id: int, user1, user2):
    """Called after a match is created — send vibe check question to both users via bot."""
    db = get_conn()
    try:
        q = await ensure_today_question(db)
    finally:
        db.close()
    BOT_TOKEN = os.getenv("TELEGRAM_BOTS_KEY", "")
    if not BOT_TOKEN:
        return
    text = (
        f"🎯 <b>Vibe Check!</b>\n\n"
        f"<b>{esc(q['question'])}</b>\n\n"
        f"Reply with your choice:\n"
        f"A — {esc(q['option_a'])}\n"
        f"B — {esc(q['option_b'])}\n\n"
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


async def _notify_vibe_result(bot, user, other_user, question, my_choice, other_choice, matched, compat_text=""):
    if not user.telegram_id:
        return
    if matched:
        text = (
            f"✨ <b>Vibe Match!</b>\n\n"
            f"You and <b>{esc(other_user.name)}</b> both chose the same!\n\n"
            f"❓ {esc(question)}\n"
            f"✅ You both: <b>{esc(my_choice)}</b>\n\n"
            f"Great minds think alike! 💕"
        )
    else:
        text = (
            f"🎭 <b>Opposites Attract!</b>\n\n"
            f"You and <b>{esc(other_user.name)}</b> chose differently!\n\n"
            f"❓ {esc(question)}\n"
            f"You: <b>{esc(my_choice)}</b>\n"
            f"{esc(other_user.name)}: <b>{esc(other_choice)}</b>\n\n"
            f"Different vibes, same spark! 💕"
        )
    try:
        await bot.send_message(chat_id=user.telegram_id, text=text + compat_text, parse_mode="HTML")
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
    active_until = _parse_dt(mystery_until)
    if active_until and active_until > now:
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
    active_until = _parse_dt(mystery_until)
    if active_until:
        active = active_until > datetime.utcnow()
    return JSONResponse({"active": active, "until": mystery_until if active else ""})
