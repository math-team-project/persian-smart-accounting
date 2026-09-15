"""
نقطه‌ی ورود FastAPI (جایگزین ``streamlit run app.py`` قدیمی برای کارگاه چک‌لیست).

اجرا:
    uvicorn api.main:app --reload

توجه: این ماژول باید بتواند ``pipeline.py`` را از ریشه‌ی پروژه import کند.
``pipeline.py`` خودش هنگام import، ریشه‌ی پروژه و زیرمسیرهای extraction_script
را به ``sys.path`` اضافه می‌کند (نگاه کنید به هدر آن فایل) پس اجرای
``uvicorn api.main:app`` از ریشه‌ی پروژه کافی است.
"""
from __future__ import annotations

import logging
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# قبل از هرچیز، متغیرهای .env را بارگذاری می‌کنیم (دقیقا مثل app.py قدیمی و
# pipeline.py که خودش هم load_dotenv() را در generate_committee_report_output
# صدا می‌زند؛ این‌جا هم صدا می‌زنیم تا سایر متغیرها -- مثل تنظیمات خودِ API --
# از همان ابتدای اجرای برنامه در دسترس باشند).
load_dotenv()

from api.config import get_settings  # noqa: E402
from api.routers import checklist, pages, summary  # noqa: E402
from api.templating import templates  # noqa: E402

logging.basicConfig(
    level=get_settings().log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(
    title="Persian Smart Accounting API",
    description="داشبورد هوشمند حسابرسی و حسابداری -- لایه‌ی API (فاز ۲: کارگاه چک‌لیست حسابرسی + خلاصه‌سازی گزارش حسابرسی)",
    version="1.0.0",
)

app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

app.include_router(pages.router)
app.include_router(checklist.router)
app.include_router(summary.router)


# هر دو کارگاه از قبل خطاهای قابل‌پیش‌بینی را در لایه‌ی سرویس (فایل نامعتبر، خطای
# اعتبارسنجی و...) با پیام فارسی مناسب مدیریت می‌کنند. این handler فقط یک
# خطای کاملاً غیرمنتظره (که هیچ کدام از handler های اختصاصی‌تر در روترها
# مدیریت نکرده‌اند) را پوشش می‌دهد تا هیچ‌وقت stack trace خام به کاربر نمایش داده
# نشود.
@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("unhandled exception on %s %s", request.method, request.url.path)
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=500,
            content={"detail": "خطای غیرمنتظره‌ای در سرور رخ داد. لطفاً دوباره تلاش کنید."},
        )
    return templates.TemplateResponse(request, "error.html", {}, status_code=500)


@app.on_event("startup")
async def _log_startup() -> None:
    logger.info("Persian Smart Accounting API started (checklist + audit-summary workspaces ready)")
