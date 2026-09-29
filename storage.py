"""Photo storage helper.

Uploads photos to a private Telegram storage chat so the app never has to
hold user photos on disk. Returns a Telegram file_id that the bot can later
send to anyone (matches, admin, etc.).
"""

import os
import asyncio
import httpx


def _bot_token() -> str:
    # Admin bot is the one with access to storage channel
    return os.getenv("ADMIN_BOT_TOKEN", "").strip().strip("'\"")


def _storage_chat_id() -> str:
    return os.getenv("TELEGRAM_STORAGE_CHAT_ID", "").strip().strip("'\"")


def is_configured() -> bool:
    return bool(_bot_token() and _storage_chat_id())


def upload_photo(file_bytes: bytes, filename: str = "photo.jpg") -> str | None:
    """Upload raw photo bytes to the storage chat. Returns a file_id or None."""
    token = _bot_token()
    chat_id = _storage_chat_id()
    if not token or not chat_id:
        return None
    try:
        # Run in thread pool if called from async context to avoid blocking
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

        def _do_upload():
            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            with httpx.Client(timeout=20) as client:
                resp = client.post(
                    url,
                    data={"chat_id": chat_id},
                    files={"photo": (filename, file_bytes, "image/jpeg")},
                )
                if resp.is_success and resp.json().get("ok"):
                    return resp.json()["result"]["photo"][-1]["file_id"]
                else:
                    err = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text[:200]
                    print(f"[STORAGE] sendPhoto failed: {err}")
            return None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(_do_upload).result(timeout=25)
        else:
            return _do_upload()
    except Exception as e:
        print(f"[STORAGE] upload failed: {e}")
    return None


async def store_photo_from_file_id(bot, file_id: str) -> str:
    """
    Re-upload photo to storage channel using forwardMessage approach.
    Returns a permanent file_id from the storage channel.
    If storage is not configured, returns the original file_id unchanged.
    """
    token = _bot_token()
    chat_id = _storage_chat_id()
    if not token:
        print("[STORAGE] TELEGRAM_BOTS_KEY not set, skipping re-upload")
        return file_id
    if not chat_id:
        print("[STORAGE] TELEGRAM_STORAGE_CHAT_ID not set, skipping re-upload")
        return file_id

    print(f"[STORAGE] uploading to chat_id={chat_id} file_id={file_id[:20]}...")
    try:
        # Directly send the file_id to storage channel — no download needed.
        # Telegram accepts an existing file_id in sendPhoto, which is instant.
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                json={"chat_id": chat_id, "photo": file_id},
            )
            data = resp.json()
            if data.get("ok"):
                stored_id = data["result"]["photo"][-1]["file_id"]
                print(f"[STORAGE] success! stored file_id: {stored_id[:20]}...")
                return stored_id
            else:
                print(f"[STORAGE] sendPhoto with file_id failed: {data.get('description')}")

        # Fallback: download bytes then re-upload
        print("[STORAGE] trying download+reupload fallback...")
        tg_file = await bot.get_file(file_id)
        async with httpx.AsyncClient(timeout=30) as client:
            dl = await client.get(
                f"https://api.telegram.org/file/bot{token}/{tg_file.file_path}"
            )
            if not dl.is_success:
                print(f"[STORAGE] download failed: HTTP {dl.status_code}")
                return file_id
            photo_bytes = dl.content
            print(f"[STORAGE] downloaded {len(photo_bytes)} bytes, uploading...")

        stored_id = await asyncio.get_event_loop().run_in_executor(
            None, lambda: upload_photo(photo_bytes)
        )
        if stored_id:
            print(f"[STORAGE] fallback success! stored file_id: {stored_id[:20]}...")
            return stored_id
        print("[STORAGE] fallback upload also failed, using original file_id")
    except Exception as e:
        print(f"[STORAGE] store_photo_from_file_id failed: {e}")
    return file_id


def upload_photo_url(url: str) -> str | None:
    """Upload a photo referenced by URL to the storage chat."""
    token = _bot_token()
    chat_id = _storage_chat_id()
    if not token or not chat_id:
        return None
    try:
        with httpx.Client(timeout=20) as client:
            img = client.get(url, timeout=15)
            if img.is_success and img.headers.get("content-type", "").startswith("image/"):
                return upload_photo(img.content, "photo.jpg")
    except Exception as e:
        print(f"[STORAGE] url upload failed: {e}")
    return None
