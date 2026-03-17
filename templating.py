from fastapi.templating import Jinja2Templates
from storage import get_photo_url

templates = Jinja2Templates(directory="templates")
templates.env.filters["photo_url"] = get_photo_url
