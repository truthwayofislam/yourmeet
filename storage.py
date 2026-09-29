"""Photo storage helper.

Uploads photos to a private Telegram storage chat so the app never has to
hold user photos on disk. Returns a Telegram file_id that the bot can later
send to anyone (matches, admin, etc.).
"""

import os
import asyncio
import httpx


def _admin_token() -> str:
    return os.getenv("ADMIN_BOT_TOKEN", "").strip().strip("'\"")


def _main_token() -> str:
    return os.getenv("TELEGRAM_BOTS_KEY", "").strip().strip("'\"")


def _storage_chat_id() -> str:
    return os.getenv("TELEGRAM_STORAGE_CHAT_ID", "").strip().strip("'\"")


def is_configured() -> bool:
    return bool(_admin_token() and _storage_chat_id())


async def store_photo_from_file_id(bot, file_id: str) -> str:
    """
    Upload photo to storage channel using admin bot.
    - Downloads photo bytes using main bot token (it owns the file_id)
    - Re-uploads to storage channel using admin bot token
    Returns admin bot's file_id (permanent, usable by admin bot).
    Falls back to original file_id on any error.
    """
    admin_token = _admin_token()
    main_token = _main_token()
    chat_id = _storage_chat_id()

    if not admin_token:
        print("[STORAGE] ADMIN_BOT_TOKEN not set, skipping")
        return file_id
    if not chat_id:
        print("[STORAGE] TELEGRAM_STORAGE_CHAT_ID not set, skipping")
        return file_id
    if not main_token:
        print("[STORAGE] TELEGRAM_BOTS_KEY not set, skipping")
        return file_id

    print(f"[STORAGE] uploading file_id={file_id[:20]}... to storage channel")
    try:
        # Step 1: Get file path using main bot (it owns the file_id)
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                f"https://api.telegram.org/bot{main_token}/getFile?file_id={file_id}"
            )
            data = r.json()
            if not data.get("ok"):
                print(f"[STORAGE] getFile failed: {data.get('description')}")
                return file_id
            file_path = data["result"]["file_path"]

        # Step 2: Download photo bytes using main bot token
        async with httpx.AsyncClient(timeout=30) as client:
            dl = await client.get(
                f"https://api.telegram.org/file/bot{main_token}/{file_path}"
            )
            if not dl.is_success:
                print(f"[STORAGE] download failed: HTTP {dl.status_code}")
                return file_id
            photo_bytes = dl.content
            print(f"[STORAGE] downloaded {len(photo_bytes)} bytes")

        # Step 3: Upload to storage channel using admin bot token
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{admin_token}/sendPhoto",
                data={"chat_id": chat_id},
                files={"photo": ("photo.jpg", photo_bytes, "image/jpeg")},
            )
            data = resp.json()
            if data.get("ok"):
                stored_id = data["result"]["photo"][-1]["file_id"]
                print(f"[STORAGE] success! admin bot file_id: {stored_id[:20]}...")
                return stored_id
            else:
                print(f"[STORAGE] admin bot upload failed: {data.get('description')}")

    except Exception as e:
        print(f"[STORAGE] store_photo_from_file_id failed: {e}")

    return file_id
