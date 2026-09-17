"""نمونه‌ی مشترک ``Jinja2Templates`` به همراه توابع/فیلترهای global مورد نیاز قالب‌ها."""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from api.utils.formatting import fa_date, fa_datetime, fa_number, fa_percent, to_persian_digits
from api.utils.icons import icon

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
TEMPLATES_DIR = WEB_DIR / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["icon"] = icon

# فیلترهای نمایشی مشترک همه‌ی قالب‌ها (ارقام/تاریخ فارسی) -- تا هیچ قالبی
# مجبور نباشد قالب‌بندی عدد و تاریخ را خودش انجام دهد.
templates.env.filters["fa_number"] = fa_number
templates.env.filters["fa_percent"] = fa_percent
templates.env.filters["fa_date"] = fa_date
templates.env.filters["fa_datetime"] = fa_datetime
templates.env.filters["fa_digits"] = to_persian_digits
