import os
import httpx

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOTS_KEY", "")
STORAGE_CHAT_ID = os.getenv("TELEGRAM_STORAGE_CHAT_ID", "")


async def upload_photo(file) -> str:
    """Upload photo to Telegram storage chat, return file_id."""
    if not TELEGRAM_BOT_TOKEN or not STORAGE_CHAT_ID:
        return ""
    try:
        content = await file.read()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                data={"chat_id": STORAGE_CHAT_ID},
                files={"photo": ("photo.jpg", content, "image/jpeg")},
            )
            if resp.status_code == 200:
                return resp.json()["result"]["photo"][-1]["file_id"]
    except Exception as e:
        print(f"[STORAGE] upload failed: {e}")
    return ""


def photo_url(file_id: str) -> str:
    """Convert file_id or path to displayable URL."""
    if not file_id:
        return ""
    if file_id.startswith("http") or file_id.startswith("/static"):
        return file_id
    return f"/photo/{file_id}"
