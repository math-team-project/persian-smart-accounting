"""روتر داشبورد: صفحه‌ی پروژه‌ها، ساخت/حذف پروژه، صفحه‌ی پروژه و صفحه‌ی هر کارگاه.

صفحه‌ی کارگاه‌ها **داینامیک** است: مسیر ``/projects/{project_id}/{slug}`` از
رجیستری کارگاه‌ها (``api/workshops/registry.py``) خوانده می‌شود، بنابراین افزودن
کارگاه جدید هیچ تغییری در این فایل یا در قالب‌ها لازم ندارد.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from api.auth.deps import require_api_project, require_page_project, require_page_user
from api.db.base import get_session
from api.db.models import Project, User
from api.repositories import projects as projects_repo
from api.schemas.dashboard import RunPanelResponse
from api.services import ai_settings
from api.services import project_service
from api.templating import templates
from api.workshops import runs
from api.workshops.pages import project_page_context
from api.workshops.registry import WORKSHOPS, workshops_with_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

NAME_REQUIRED = "نام پروژه را وارد کنید."
NAME_TOO_LONG = "نام پروژه بیش از حد بلند است (حداکثر ۲۰۰ نویسه)."
CONFIRM_REQUIRED = "تأیید حذف نامعتبر است."


# ---------------------------------------------------------------------------
# داشبورد: فهرست پروژه‌های کاربر
# ---------------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    user: User = Depends(require_page_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    return _render_dashboard(request, user, session)


def _render_dashboard(
    request: Request,
    user: User,
    session: Session,
    *,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    project_rows = projects_repo.list_for_user(session, user.id)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "projects": [
                {
                    "id": project.id,
                    "name": project.name,
                    "created_at": project.created_at,
                    "run_count": run_count,
                }
                for project, run_count in project_rows
            ],
            "workshops": list(WORKSHOPS.values()),
            "error": error,
        },
        status_code=status_code,
    )


@router.post("/projects")
async def create_project(
    request: Request,
    name: str = Form(""),
    user: User = Depends(require_page_user),
    session: Session = Depends(get_session),
):
    """ساخت پروژه‌ی جدید و رفتن مستقیم به صفحه‌ی همان پروژه."""
    error = _validate_name(name)
    if error:
        return _render_dashboard(request, user, session, error=error, status_code=400)

    project = projects_repo.create(session, user.id, name)
    logger.info("project %s created for user %s", project.id, user.id)
    return RedirectResponse(f"/projects/{project.id}", status_code=303)


def _validate_name(name: str) -> str | None:
    if not name or not name.strip():
        return NAME_REQUIRED
    if len(name.strip()) > 200:
        return NAME_TOO_LONG
    return None


@router.post("/projects/{project_id}/delete")
async def delete_project(
    project: Project = Depends(require_page_project),
    confirm: str = Form(""),
    session: Session = Depends(get_session),
):
    """حذف کامل پروژه (پایگاه‌داده + فایل‌های نتیجه) -- بازگشت‌پذیر نیست.

    فیلد ``confirm`` از فرم تأیید می‌آید؛ بدون آن درخواست رد می‌شود تا یک ارسال
    تصادفی/ناقص هرگز داده‌ای را پاک نکند.
    """
    if confirm != "delete":
        raise HTTPException(status_code=400, detail=CONFIRM_REQUIRED)

    # شناسه پیش از حذف نگه داشته می‌شود (پس از حذف، شیء ORM دیگر معتبر نیست).
    project_id = project.id
    summary = project_service.delete_project(session, project)
    logger.info("project %s deleted via dashboard (%s)", project_id, summary)
    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------------------
# صفحه‌ی پروژه: کارت‌های کارگاه‌ها + پنل کارهای در جریان + تاریخچه
# ---------------------------------------------------------------------------
@router.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_page(
    request: Request,
    project: Project = Depends(require_page_project),
    user: User = Depends(require_page_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    # تاریخچه از همان ابتدا داخل خودِ صفحه رندر می‌شود (نه فقط با جاوااسکریپت)؛
    # پنل کارهای در جریان بعداً با ``/projects/{id}/history`` آن را تازه می‌کند.
    # ``ai_settings_panels`` فقط شامل کارگاه‌هایی است که تنظیمات اختصاصی‌شان
    # واقعاً اعمال می‌شود؛ هیچ رازی (خود کلید API) در این داده نیست.
    return templates.TemplateResponse(
        request,
        "project.html",
        project_page_context(
            project,
            user,
            runs=runs.runs_for_history(session, project.id),
            ai_settings_panels=[
                ai_settings.panel_for(session, project.id, workshop)
                for workshop in workshops_with_settings()
            ],
        ),
    )


@router.get("/projects/{project_id}/history", response_class=HTMLResponse)
async def project_history_fragment(
    request: Request,
    project: Project = Depends(require_page_project),
    user: User = Depends(require_page_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """تکه‌ی HTML تاریخچه -- پنل کارهای در جریان پس از پایان هر اجرا آن را تازه می‌کند."""
    return _render_history(request, project, session)


def _render_history(request: Request, project: Project, session: Session) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "components/run_history.html",
        {
            "project": project,
            "runs": runs.runs_for_history(session, project.id),
        },
    )


@router.get("/api/projects/{project_id}/jobs", response_model=RunPanelResponse)
async def project_jobs_panel(
    project: Project = Depends(require_api_project),
    session: Session = Depends(get_session),
) -> RunPanelResponse:
    """وضعیت زنده‌ی اجراهای همین پروژه (همه‌ی کارگاه‌ها با هم).

    چون وضعیت job ها سمت سرور نگه داشته می‌شود (نه در حافظه‌ی مرورگر)، کاربر
    می‌تواند از کارگاه A به کارگاه B برود، صفحه را دوباره بارگذاری کند یا حتی
    مرورگر را ببندد و بعد وضعیت هر دو اجرا را همان‌طور که هست ببیند.
    """
    return RunPanelResponse(project_id=project.id, jobs=runs.panel_runs(session, project.id))


# ---------------------------------------------------------------------------
# صفحه‌ی هر کارگاه -- از روی رجیستری، نه هاردکد
# ---------------------------------------------------------------------------
@router.get("/projects/{project_id}/{workshop_slug}", response_class=HTMLResponse)
async def workshop_page(
    request: Request,
    workshop_slug: str,
    project: Project = Depends(require_page_project),
    user: User = Depends(require_page_user),
) -> HTMLResponse:
    workshop = WORKSHOPS.get(workshop_slug)
    if workshop is None:
        raise HTTPException(status_code=404, detail="کارگاه مورد نظر یافت نشد.")
    return workshop.page_renderer(request, project, user)
