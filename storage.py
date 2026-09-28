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
            if resp.is_success and resp.json().get("ok"):
                return resp.json()["result"]["photo"][-1]["file_id"]
    except Exception as e:
        print(f"[STORAGE] upload failed: {e}")
    return None


async def store_photo_from_file_id(bot, file_id: str) -> str:
    """
    Download photo from Telegram using file_id, re-upload to storage channel.
    Returns a permanent file_id from the storage channel.
    If storage is not configured, returns the original file_id unchanged.
    """
    if not is_configured():
        return file_id
    try:
        # Download the photo bytes via bot
        tg_file = await bot.get_file(file_id)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"https://api.telegram.org/file/bot{BOT_TOKEN}/{tg_file.file_path}"
            )
            if not resp.is_success:
                print(f"[STORAGE] download failed: HTTP {resp.status_code}")
                return file_id
            photo_bytes = resp.content

        # Re-upload to storage channel
        stored_id = upload_photo(photo_bytes)
        if stored_id:
            print(f"[STORAGE] stored photo: {stored_id[:20]}...")
            return stored_id
    except Exception as e:
        print(f"[STORAGE] store_photo_from_file_id failed: {e}")
    # Fallback — use original file_id
    return file_id


def upload_photo_url(url: str) -> str | None:
    """Upload a photo referenced by URL to the storage chat."""
    if not is_configured():
        return None
    try:
        with httpx.Client(timeout=20) as client:
            img = client.get(url, timeout=15)
            if img.is_success and img.headers.get("content-type", "").startswith("image/"):
                return upload_photo(img.content, "photo.jpg")
    except Exception as e:
        print(f"[STORAGE] url upload failed: {e}")
    return None