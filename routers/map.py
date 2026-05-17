from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, HTMLResponse
from database import get_db, row_to_user, USER_COLS
from routers.auth import get_current_user
from storage import photo_url
from templating import templates

router = APIRouter()

_COLS = ", ".join(USER_COLS)


@router.get("/map", response_class=HTMLResponse)
async def map_page(request: Request, current_user=Depends(get_current_user)):
    if not current_user:
        return HTMLResponse(status_code=302, headers={"Location": "/setup"})
    return templates.TemplateResponse(
        request, "map.html", {"user": current_user, "active": "map"}
    )


@router.get("/api/map/match")
async def get_map_match(request: Request, db=Depends(get_db), current_user=Depends(get_current_user)):
    """Return a random approved user for map discovery."""
    if not current_user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    liked = [r[0] for r in db.execute("SELECT to_user FROM likes WHERE from_user=?", (current_user.id,)).fetchall()]
    skipped = [r[0] for r in db.execute("SELECT skipped_id FROM skips WHERE user_id=?", (current_user.id,)).fetchall()]
    excluded = list(set(liked + skipped + [current_user.id]))
    placeholders = ",".join("?" * len(excluded))

    interested_in = getattr(current_user, "interested_in", "both") or "both"
    if interested_in == "both":
        gender_sql = "gender IN ('male','female')"
        gender_params = ()
    else:
        gender_sql = "gender=?"
        gender_params = (interested_in,)

    row = db.execute(
        f"""SELECT {_COLS} FROM users
            WHERE id NOT IN ({placeholders})
            AND {gender_sql}
            AND age >= 18
            AND is_blocked=0 AND is_rejected=0 AND is_approved=1
            AND lat != 0 AND lng != 0
            ORDER BY RANDOM()
            LIMIT 1""",
        (*excluded, *gender_params),
    ).fetchone()

    if not row:
        # Try without lat/lng filter
        row = db.execute(
            f"""SELECT {_COLS} FROM users
                WHERE id NOT IN ({placeholders})
                AND {gender_sql}
                AND age >= 18
                AND is_blocked=0 AND is_rejected=0 AND is_approved=1
                ORDER BY RANDOM()
                LIMIT 1""",
            (*excluded, *gender_params),
        ).fetchone()

    if not row:
        return JSONResponse({"error": "no_users"})

    user = row_to_user(row)
    import json
    photos = json.loads(user.photos or "[]")

    return JSONResponse({
        "id": user.id,
        "name": user.name,
        "age": user.age,
        "city": user.city or "",
        "bio": user.bio or "",
        "photo": photo_url(user.photo),
        "photos": [photo_url(p) for p in photos],
        "interests": json.loads(user.interests or "[]"),
        "is_verified": user.is_verified,
        "lat": user.lat or 20.0,
        "lng": user.lng or 0.0,
    })
