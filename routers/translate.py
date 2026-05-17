import os
import json
import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
MODEL = "google/gemma-3-27b-it"


@router.post("/api/translate")
async def translate(request_data: dict):
    lang = request_data.get("lang", "en")
    strings = request_data.get("strings", {})

    if lang == "en" or not strings:
        return JSONResponse(strings)

    if not OPENROUTER_API_KEY:
        return JSONResponse(strings)

    strings_json = json.dumps(strings, ensure_ascii=False)
    prompt = (
        f"Translate the following JSON values to {lang} language. "
        f"Keep all JSON keys exactly the same. Only translate the values. "
        f"Return only valid JSON, no explanation.\n\n{strings_json}"
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                },
            )
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"].strip()
                # Extract JSON from response
                start = content.find("{")
                end = content.rfind("}") + 1
                if start != -1 and end > start:
                    translated = json.loads(content[start:end])
                    return JSONResponse(translated)
    except Exception as e:
        print(f"[TRANSLATE] error: {e}")

    # Fallback — return original English strings
    return JSONResponse(strings)
