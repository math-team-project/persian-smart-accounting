"""
نقطه‌ی ورود FastAPI (نسخه‌ی داشبورد/پروژه‌ها).

اجرا:
    uvicorn api.main:app --reload

توجه: این ماژول باید بتواند ``pipeline.py`` را از ریشه‌ی پروژه import کند.
``pipeline.py`` خودش هنگام import، ریشه‌ی پروژه و زیرمسیرهای extraction_script
را به ``sys.path`` اضافه می‌کند (نگاه کنید به هدر آن فایل) پس اجرای
``uvicorn api.main:app`` از ریشه‌ی پروژه کافی است.

نکته‌ی معماری: روتر کارگاه‌ها این‌جا **نام‌برده** نمی‌شود. همه‌ی کارگاه‌ها از
رجیستری (``api.workshops``) خوانده و روترهایشان در یک حلقه ثبت می‌شوند؛ افزودن
کارگاه جدید فقط یک ورودی در رجیستری است و این فایل تغییری لازم ندارد.
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

# قبل از هرچیز، متغیرهای .env را بارگذاری می‌کنیم (دقیقا مثل app.py قدیمی و
# pipeline.py که خودش هم load_dotenv() را در generate_committee_report_output
# صدا می‌زند؛ این‌جا هم صدا می‌زنیم تا سایر متغیرها -- مثل تنظیمات خودِ API --
# از همان ابتدای اجرای برنامه در دسترس باشند).
load_dotenv()

from api.auth.deps import LoginRequired  # noqa: E402
from api.auth.deps import current_user  # noqa: E402
from api.config import get_settings  # noqa: E402
from api.db.base import SessionLocal, init_db  # noqa: E402
from api.repositories import chat_messages as chat_messages_repo  # noqa: E402
from api.routers import auth as auth_router  # noqa: E402
from api.routers import dashboard  # noqa: E402
from api.routers import settings as settings_router  # noqa: E402
from api.templating import templates  # noqa: E402
from api.workshops import WORKSHOPS  # noqa: E402
from api.workshops import runs as workshop_runs  # noqa: E402

settings = get_settings()

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(
    title="Persian Smart Accounting API",
    description=(
        "داشبورد هوشمند حسابرسی و حسابداری -- لایه‌ی API (داشبورد پروژه‌محور: "
        "ورود کاربر، پروژه‌ها، تاریخچه‌ی اجراها و کارگاه‌های چک‌لیست حسابرسی و "
        "خلاصه‌سازی گزارش حسابرسی)"
    ),
    version="2.0.0",
)

# نشست روی کوکی امضاشده؛ ``SameSite=Lax`` جلوی همراه‌شدن کوکی با POST های
# بین‌سایتی را می‌گیرد (سد CSRF متناسب با این MVP) و ``https_only`` برای اجرای
# محلی روی http خاموش است.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="psa_session",
    max_age=settings.session_max_age_seconds,
    same_site="lax",
    https_only=False,
)


class _RevalidatingStaticFiles(StaticFiles):
    """فایل‌های استاتیک با ``Cache-Control: no-cache`` سرو می‌شوند.

    این پروژه هیچ مرحله‌ی build و هیچ هش محتوایی در نام فایل‌ها ندارد؛ بنابراین
    بدون این هدر مرورگر می‌تواند تا مدت نامعلومی نسخه‌ی قدیمی ``.js``/``.css`` را
    از حافظه‌ی خود اجرا کند و کاربر پس از هر به‌روزرسانی، رفتار قدیمی را ببیند
    (باگ ظاهری «فایل را عوض کردم ولی تغییر نمی‌بینم»).

    ``no-cache`` یعنی «قبل از استفاده اعتبارسنجی کن»، نه «ذخیره نکن»: چون
    ``StaticFiles`` خودش ``ETag`` و ``Last-Modified`` می‌فرستد، پاسخ معمول
    ``304 Not Modified`` است -- هزینه‌ی شبکه تقریباً صفر، اما همیشه نسخه‌ی درست.
    """

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount(
    "/static",
    _RevalidatingStaticFiles(directory=str(WEB_DIR / "static")),
    name="static",
)

app.include_router(auth_router.router)
app.include_router(dashboard.router)
app.include_router(settings_router.router)
for workshop in WORKSHOPS.values():
    app.include_router(workshop.router)

# جدول‌های پایگاه‌داده (idempotent) -- در زمان import ساخته می‌شوند تا هم اجرای
# واقعی و هم TestClient بدون اجرای lifespan کار کنند.
init_db()
with SessionLocal() as _session:
    workshop_runs.mark_orphaned_runs(_session)
    # قرینه‌ی بالا برای چت‌بات مالی: هر پاسخ ``pending`` باقی‌مانده از فرآیند قبلی
    # (پرسش پس‌زمینه‌ی این کارگاه در حافظه‌ی همین فرآیند بود) ناموفق علامت می‌خورد،
    # وگرنه UI برای همیشه «در حال پاسخ‌گویی» نشان می‌داد.
    chat_messages_repo.mark_pending_messages_failed(
        _session,
        error_message="پاسخ‌دهی به دلیل راه‌اندازی مجدد سرور ناتمام ماند. لطفاً پرسش را دوباره بفرستید.",
    )


@app.exception_handler(LoginRequired)
async def _login_required_handler(request: Request, exc: LoginRequired) -> RedirectResponse:
    """کاربر وارد نشده: هدایت به صفحه‌ی ورود با بازگشت به همان مسیر."""
    return RedirectResponse(f"/login?next={quote(exc.next_url)}", status_code=303)


# ---------------------------------------------------------------------------
# خطاهای «قابل‌پیش‌بینی» (۴۰۴، ۴۰۳، ۴۰۱ و...)
# ---------------------------------------------------------------------------
# عنوان فارسی هر کد وضعیت برای نمایش در صفحه‌ی خطا. کدهای فهرست‌نشده یک عنوان
# عمومی می‌گیرند تا هیچ‌وقت متن انگلیسی یا خام به کاربر نشان داده نشود.
_PAGE_ERROR_TITLES: dict[int, str] = {
    400: "درخواست نامعتبر",
    401: "برای ادامه باید وارد شوید",
    403: "دسترسی به این بخش مجاز نیست",
    404: "این صفحه پیدا نشد",
    405: "این عملیات در این نشانی مجاز نیست",
    409: "این عملیات در وضعیت فعلی امکان‌پذیر نیست",
    413: "حجم درخواست بیش از حد مجاز است",
    422: "اطلاعات ارسالی نامعتبر است",
}

_PAGE_ERROR_MESSAGES: dict[int, str] = {
    401: "برای دسترسی به این بخش باید وارد حساب کاربری خود شوید.",
    403: "حساب کاربری شما اجازه‌ی دسترسی به این بخش را ندارد.",
    404: (
        "نشانی درخواستی وجود ندارد، یا موردی که به دنبال آن هستید حذف شده است. "
        "اگر این نشانی را از پیش نشانه‌گذاری کرده‌اید، لطفاً از فهرست پروژه‌ها مسیر درست را باز کنید."
    ),
}


def _has_persian(text: str) -> bool:
    """آیا متن نویسه‌ی فارسی/عربی دارد؟ (برای تشخیص پیام‌های آماده‌ی فارسی از
    متن‌های خام انگلیسی مانند ``Not Found`` که Starlette خودش می‌سازد.)"""
    return any("\u0600" <= char <= "\u06ff" for char in text)


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    """خطای HTTP: مسیرهای JSON همان پاسخ ساخت‌یافته‌ی قبلی را می‌گیرند و
    مسیرهای HTML یک صفحه‌ی فارسی با راه بازگشت -- به‌جای JSON خام.

    تفکیک بر اساس پیشوند ``/api/`` است، چون کلاینت‌های جاوااسکریپت به کلید
    ``detail`` تکیه می‌کنند (``parseErrorDetail`` در فایل‌های JS) و نباید HTML
    دریافت کنند.
    """
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            {"detail": exc.detail},
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )

    detail = exc.detail if isinstance(exc.detail, str) else ""
    title = _PAGE_ERROR_TITLES.get(exc.status_code, "خطای غیرمنتظره‌ای رخ داد")
    # پیام اختصاصی خودِ روتر (اگر فارسی باشد) بر پیام پیش‌فرض مقدم است؛ متن خام
    # انگلیسی هرگز به کاربر نمایش داده نمی‌شود.
    message = (
        detail
        if _has_persian(detail)
        else _PAGE_ERROR_MESSAGES.get(
            exc.status_code,
            "متأسفانه انجام این درخواست ممکن نشد. لطفاً دوباره تلاش کنید.",
        )
    )

    # اگر کاربر وارد شده باشد، سربرگ/ناوبری همان‌طور که هست نمایش داده می‌شود.
    # این بخش کاملاً تدافعی است: خطا در ساختن بافتار نباید خودِ صفحه‌ی خطا را
    # از کار بیندازد.
    context: dict[str, object] = {
        "status_code": exc.status_code,
        "page_title": title,
        "page_message": message,
    }
    try:
        with SessionLocal() as session:
            user = current_user(request, session)
            if user is not None:
                context["user"] = user
                context["workshops"] = list(WORKSHOPS.values())
    except Exception:  # noqa: BLE001 -- صفحه‌ی خطا هرگز نباید به‌خاطر بافتار بشکند
        logger.warning("building error page context failed", exc_info=True)

    return templates.TemplateResponse(
        request, "error_page.html", context, status_code=exc.status_code
    )


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
    if settings.using_default_secret_key:
        logger.warning(
            "PSA_SECRET_KEY تنظیم نشده است؛ از کلید پیش‌فرض توسعه استفاده می‌شود. "
            "پیش از استقرار واقعی این مقدار را در .env تنظیم کنید."
        )
    logger.info(
        "Persian Smart Accounting API started (%d workshops ready: %s)",
        len(WORKSHOPS),
        ", ".join(WORKSHOPS),
    )
