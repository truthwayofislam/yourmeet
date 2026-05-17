from fastapi.templating import Jinja2Templates
from storage import get_photo_url
import json as _json

templates = Jinja2Templates(directory="templates")
templates.env.filters["photo_url"] = get_photo_url
templates.env.filters["from_json"] = lambda s: (_json.loads(s) if s else [])
