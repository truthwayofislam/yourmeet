import os
import httpx
from fastapi import UploadFile

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
STORAGE_CHAT_ID = os.getenv("TELEGRAM_STORAGE_CHAT_ID", "")  # Private channel/group ID

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

async def upload_photo_to_telegram(file: UploadFile) -> str:
    """
    Upload image to Telegram private channel.
    Returns permanent public URL via getFile API.
    """
    if not BOT_TOKEN or not STORAGE_CHAT_ID:
        return ""

    contents = await file.read()
    await file.seek(0)

    async with httpx.AsyncClient(timeout=30) as client:
        # Upload to Telegram
        resp = await client.post(
            f"{TELEGRAM_API}/sendPhoto",
            data={"chat_id": STORAGE_CHAT_ID},
            files={"photo": (file.filename, contents, file.content_type or "image/jpeg")}
        )
        if not resp.is_success:
            return ""

        result = resp.json()
        if not result.get("ok"):
            return ""

        # Get largest photo size
        photos = result["result"]["photo"]
        file_id = photos[-1]["file_id"]

        # Get file path
        file_resp = await client.get(f"{TELEGRAM_API}/getFile?file_id={file_id}")
        file_data = file_resp.json()
        if not file_data.get("ok"):
            return file_id  # fallback: store file_id

        file_path = file_data["result"]["file_path"]
        return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"


def get_photo_url(photo: str) -> str:
    """
    Convert stored photo value to displayable URL.
    Handles: Telegram URL, old local path, empty
    """
    if not photo:
        return ""
    if photo.startswith("http"):
        return photo
    if photo.startswith("static/"):
        return f"/{photo}"
    return ""
