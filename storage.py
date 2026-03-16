import os
import httpx
from fastapi import UploadFile

def _get_telegram_api():
    token = os.getenv("BOT_TOKEN", "")
    return f"https://api.telegram.org/bot{token}", token

async def upload_photo_to_telegram(file: UploadFile) -> str:
    telegram_api, token = _get_telegram_api()
    storage_chat_id = os.getenv("TELEGRAM_STORAGE_CHAT_ID", "")

    if not token or not storage_chat_id:
        return ""

    contents = await file.read()
    await file.seek(0)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{telegram_api}/sendPhoto",
            data={"chat_id": storage_chat_id},
            files={"photo": (file.filename, contents, file.content_type or "image/jpeg")}
        )
        if not resp.is_success:
            return ""
        result = resp.json()
        if not result.get("ok"):
            return ""

        file_id = result["result"]["photo"][-1]["file_id"]
        file_resp = await client.get(f"{telegram_api}/getFile?file_id={file_id}")
        file_data = file_resp.json()
        if not file_data.get("ok"):
            return file_id

        file_path = file_data["result"]["file_path"]
        return f"https://api.telegram.org/file/bot{token}/{file_path}"


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
