import os
import httpx
import base64


async def upload_photo(file) -> str:
    """Upload photo to Telegram storage chat, return file_id."""
    bot_token = os.getenv("TELEGRAM_BOTS_KEY", "").strip().strip("'\"")
    storage_chat_id = os.getenv("TELEGRAM_STORAGE_CHAT_ID", "").strip().strip("'\"")
    if not bot_token or not storage_chat_id:
        print(f"[STORAGE] missing env — TELEGRAM_BOTS_KEY={'set' if bot_token else 'MISSING'}, TELEGRAM_STORAGE_CHAT_ID={'set' if storage_chat_id else 'MISSING'}")
        return ""
    try:
        content = await file.read()
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendPhoto",
                data={"chat_id": storage_chat_id},
                files={"photo": (file.filename or "photo.jpg", content, file.content_type or "image/jpeg")},
            )
            print(f"[STORAGE] upload response: {resp.status_code} — {resp.text[:300]}")
            if resp.status_code == 200:
                return resp.json()["result"]["photo"][-1]["file_id"]
    except Exception as e:
        print(f"[STORAGE] upload failed: {e}")
    return ""


def photo_url(file_id: str) -> str:
    """Convert file_id or path to displayable URL."""
    if not file_id:
        return ""
    if file_id.startswith("http") or file_id.startswith("/"):
        return file_id
    return f"/photo/{file_id}"
