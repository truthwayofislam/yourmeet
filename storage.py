"""Photo storage helper.

Uploads photos to a private Telegram storage chat so the app never has to
hold user photos on disk. Returns a Telegram file_id that the bot can later
send to anyone (matches, admin, etc.).
"""

import os
import httpx

STORAGE_CHAT_ID = os.getenv("TELEGRAM_STORAGE_CHAT_ID", "").strip().strip("'\"")
BOT_TOKEN = os.getenv("TELEGRAM_BOTS_KEY", "").strip().strip("'\"")


def is_configured() -> bool:
    return bool(BOT_TOKEN and STORAGE_CHAT_ID)


def upload_photo(file_bytes: bytes, filename: str = "photo.jpg") -> str | None:
    """Upload raw photo bytes to the storage chat. Returns a file_id or None."""
    if not is_configured():
        return None
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                url,
                data={"chat_id": STORAGE_CHAT_ID},
                files={"photo": (filename, file_bytes, "image/jpeg")},
            )
            if resp.is_ok and resp.json().get("ok"):
                return resp.json()["result"]["photo"][-1]["file_id"]
    except Exception as e:
        print(f"[STORAGE] upload failed: {e}")
    return None


def upload_photo_url(url: str) -> str | None:
    """Upload a photo referenced by URL to the storage chat."""
    if not is_configured():
        return None
    try:
        with httpx.Client(timeout=20) as client:
            img = client.get(url, timeout=15)
            if img.is_ok and img.headers.get("content-type", "").startswith("image/"):
                return upload_photo(img.content, "photo.jpg")
    except Exception as e:
        print(f"[STORAGE] url upload failed: {e}")
    return None