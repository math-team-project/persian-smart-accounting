"""نمونه‌ی مشترک ``Jinja2Templates`` به همراه توابع/فیلترهای global مورد نیاز قالب‌ها."""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from api.utils.icons import icon

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
TEMPLATES_DIR = WEB_DIR / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["icon"] = icon
