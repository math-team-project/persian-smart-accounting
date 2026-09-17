"""صفحه‌های ورود، خروج و راه‌اندازی اولیه (ساخت نخستین کاربر).

نشست روی کوکی امضاشده‌ی ``SessionMiddleware`` سوار است (نگاه کنید
``api/auth/deps.py``). هیچ OAuth/JWT/تأیید ایمیلی‌ای وجود ندارد -- این یک MVP
تیمی/دانشجویی است، نه یک محصول عمومی.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from api.auth import passwords
from api.auth.deps import current_user, login_user, logout_user
from api.db.base import get_session
from api.repositories import users as users_repo
from api.templating import templates

router = APIRouter(tags=["auth"])

# پیام‌های خطای فارسی صفحه‌ی ورود/راه‌اندازی (منبع واحد، تا آزمون‌ها هم به متن
# وابسته نشوند و همیشه یک‌جا تغییر کنند).
WRONG_CREDENTIALS = "نام کاربری یا رمز عبور نادرست است."
SETUP_ALREADY_DONE = "کاربر راهبر قبلاً ساخته شده است. لطفاً وارد شوید."
USERNAME_REQUIRED = "نام کاربری را وارد کنید."
PASSWORD_TOO_SHORT = f"رمز عبور باید حداقل {passwords.MIN_PASSWORD_LENGTH} نویسه باشد."
PASSWORD_MISMATCH = "تکرار رمز عبور مطابقت ندارد."


def _safe_next(next_url: str | None) -> str:
    """فقط مسیرهای داخلی به‌عنوان مقصد پس از ورود پذیرفته می‌شوند (ضد open-redirect)."""
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        return "/"
    return next_url


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    next: str | None = None,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    if current_user(request, session) is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "mode": "login",
            "next": _safe_next(next),
            # اگر هنوز هیچ کاربری ساخته نشده، صفحهٔ ورود راهی برای رفتن به
            # راه‌اندازی اولیه نشان می‌دهد (به‌جای اینکه کاربر در بن‌بست بماند).
            "setup_available": users_repo.count_users(session) == 0,
        },
    )


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
    session: Session = Depends(get_session),
):
    user = users_repo.get_by_username(session, username)
    if user is None or not passwords.verify_password(password, user.password_hash):
        # پاسخ یکسان برای «کاربر ناموجود» و «رمز نادرست» -- تا وجود نام کاربری لو نرود.
        return templates.TemplateResponse(
            request,
            "login.html",
            {"mode": "login", "error": WRONG_CREDENTIALS, "username": username, "next": _safe_next(next)},
            status_code=400,
        )

    if passwords.needs_rehash(user.password_hash):
        users_repo.update_password_hash(session, user, passwords.hash_password(password))

    login_user(request, user)
    return RedirectResponse(_safe_next(next), status_code=303)


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    logout_user(request)
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------------------
# راه‌اندازی اولیه: ساخت نخستین کاربر
# ---------------------------------------------------------------------------
@router.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, session: Session = Depends(get_session)):
    if users_repo.count_users(session) > 0:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"mode": "setup"})


@router.post("/setup")
async def setup_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    password_confirm: str = Form(""),
    session: Session = Depends(get_session),
):
    """ساخت نخستین کاربر -- تنها زمانی کار می‌کند که هنوز هیچ کاربری وجود نداشته باشد.

    پس از ساخت، کاربر مستقیماً وارد می‌شود (نیازی به یک ورود اضافه نیست).
    """
    if users_repo.count_users(session) > 0:
        return RedirectResponse("/login", status_code=303)

    error = _validate_new_user(username, password, password_confirm)
    if error:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"mode": "setup", "error": error, "username": username},
            status_code=400,
        )

    user = users_repo.create(session, username, passwords.hash_password(password))
    login_user(request, user)
    return RedirectResponse("/", status_code=303)


def _validate_new_user(username: str, password: str, password_confirm: str) -> str | None:
    if not username.strip():
        return USERNAME_REQUIRED
    if len(password) < passwords.MIN_PASSWORD_LENGTH:
        return PASSWORD_TOO_SHORT
    if password != password_confirm:
        return PASSWORD_MISMATCH
    return None
