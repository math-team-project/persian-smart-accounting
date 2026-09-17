"""وابستگی‌های FastAPI برای احراز هویت و دسترسی به پروژه.

نشست روی کوکی امضاشده‌ی ``SessionMiddleware`` سوار می‌شود و فقط شناسه‌ی کاربر را
نگه می‌دارد (``user_id``)؛ خودِ کاربر از پایگاه‌داده خوانده می‌شود تا تغییر رمز یا
حذف کاربر بی‌درنگ اثر کند. کوکی با ``SameSite=Lax`` تنظیم می‌شود، بنابراین
درخواست‌های POST بین‌سایتی کوکی را همراه نمی‌برند و همان‌قدر برای این MVP در برابر
CSRF کافی است (هیچ endpoint مخصوصی برای CSRF جدا لازم نیست).
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from api.db.base import get_session
from api.db.models import Project, User
from api.repositories import projects as projects_repo
from api.repositories import users as users_repo

SESSION_USER_KEY = "user_id"


class LoginRequired(Exception):
    """کاربر وارد نشده است؛ برای صفحه‌های HTML به ``/login`` هدایت می‌شود."""

    def __init__(self, next_url: str = "/") -> None:
        super().__init__("login required")
        self.next_url = next_url


# ---------------------------------------------------------------------------
# نشست
# ---------------------------------------------------------------------------
def login_user(request: Request, user: User) -> None:
    request.session[SESSION_USER_KEY] = user.id


def logout_user(request: Request) -> None:
    request.session.clear()


def current_user(request: Request, session: Session) -> User | None:
    """کاربر جاری یا ``None`` -- تنها نقطه‌ی خواندن نشست."""
    user_id = request.session.get(SESSION_USER_KEY)
    if not isinstance(user_id, int):
        return None
    return users_repo.get(session, user_id)


# ---------------------------------------------------------------------------
# وابستگی‌ها
# ---------------------------------------------------------------------------
def require_page_user(request: Request, session: Session = Depends(get_session)) -> User:
    """برای مسیرهای HTML: در صورت نبود نشست، به صفحه‌ی ورود هدایت می‌کند."""
    user = current_user(request, session)
    if user is None:
        raise LoginRequired(next_url=request.url.path)
    return user


def require_api_user(request: Request, session: Session = Depends(get_session)) -> User:
    """برای مسیرهای JSON: در صورت نبود نشست، پاسخ 401 برمی‌گرداند."""
    user = current_user(request, session)
    if user is None:
        raise HTTPException(status_code=401, detail="برای دسترسی به این بخش باید وارد شوید.")
    return user


def _owned_project(project_id: int, user: User, session: Session) -> Project:
    project = projects_repo.get_owned(session, project_id, user.id)
    if project is None:
        raise HTTPException(status_code=404, detail="پروژه یافت نشد.")
    return project


def require_page_project(
    project_id: int,
    user: User = Depends(require_page_user),
    session: Session = Depends(get_session),
) -> Project:
    return _owned_project(project_id, user, session)


def require_api_project(
    project_id: int,
    user: User = Depends(require_api_user),
    session: Session = Depends(get_session),
) -> Project:
    return _owned_project(project_id, user, session)
