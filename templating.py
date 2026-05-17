import json
from fastapi.templating import Jinja2Templates
from storage import photo_url

templates = Jinja2Templates(directory="templates")
templates.env.filters["photo_url"] = photo_url
templates.env.filters["from_json"] = lambda s: (json.loads(s) if s else [])
