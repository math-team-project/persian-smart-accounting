"""کارگاه «تحلیل بودجه» به‌عنوان یک ورودی رجیستری.

ساختار دقیقاً موازی ``api/workshops/checklist.py`` و ``api/workshops/audit_summary.py``
است: endpoint ها زیر ``/api/projects/{project_id}/budget-analysis/...`` قرار دارند،
هر اجرا یک ردیف ``workshop_runs`` می‌سازد و نتیجه‌اش (گزارش مدیریتی Word + متادیتای
شمارشی) در همان ردیف ثبت می‌شود.

منطق پردازش در پکیج ``budget_analysis`` است (سه مرحله: استخراج ساختارمند ← تحلیل
با خروجی JSON اعتبارسنجی‌شده ← رندر قطعی Word). این ماژول فقط آداپتور HTTP است.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

# نکته: ``await request.form()`` همیشه نمونه‌هایی از
# ``starlette.datastructures.UploadFile`` برمی‌گرداند (نه زیرکلاس ``fastapi.UploadFile``)،
# به همین دلیل برای بررسی ``isinstance`` همین کلاس پایه ایمپورت می‌شود.
from starlette.datastructures import UploadFile

from api.auth.deps import require_api_project
from api.config import get_settings
from api.db.base import get_session
from api.db.models import Project, User
from api.repositories import workshop_runs as runs_repo
from api.schemas.budget import JobResultsResponse, JobStatusResponse, StartJobResponse
# نکته: مانند ``api/workshops/checklist.py`` و ``api/workshops/audit_summary.py``، سرویس
# این کارگاه به‌صورت ماژول ایمپورت می‌شود و اعضایش در زمان فراخوانی خوانده می‌شوند.
# (این سه ماژول و سرویس‌هایشان یک چرخه‌ی ایمپورت شناخته‌شده و از قبل موجود دارند؛
# ترتیب واقعی ایمپورت در ``api/main.py`` آن را هرگز فعال نمی‌کند: پکیج
# ``api.workshops`` پیش از هر سرویسی ایمپورت می‌شود.)
from api.services import budget_service
from api.templating import templates
from api.utils.downloads import docx_response
from api.utils.jobs import get_job_or_404
from api.workshops import runs
from api.workshops.pages import workshop_page_context
from api.workshops.registry import WorkshopDefinition, register

# نام این کارگاه تنها در پکیج ``budget_analysis`` تعریف شده است (نه رشته‌ی تکرارشده).
from budget_analysis import SLUG

logger = logging.getLogger(__name__)

router = APIRouter(prefix=f"/api/projects/{{project_id}}/{SLUG}", tags=[SLUG])


# ---------------------------------------------------------------------------
# صفحه‌ی HTML کارگاه (داخل یک پروژه)
# ---------------------------------------------------------------------------
def render_page(request: Request, project: Project, user: User) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        WORKSHOP.page_template,
        workshop_page_context(
            project,
            user,
            WORKSHOP,
            slots=budget_service.get_file_slots(),
        ),
    )


# ---------------------------------------------------------------------------
# API کارگاه
# ---------------------------------------------------------------------------
@router.post("/jobs", response_model=StartJobResponse)
async def create_budget_job(
    request: Request,
    project: Project = Depends(require_api_project),
    session: Session = Depends(get_session),
) -> StartJobResponse:
    """شروع یک اجرای جدید تحلیل بودجه برای این پروژه.

    فرم شامل دو فایل الزامی (کلیدهای ``budget_base_year`` و ``budget_current_year``)
    و چند فیلد متنی اختیاری (نام سازمان، سال پایه، سال جاری، توضیح جلسه) است.
    """
    settings = get_settings()
    form = await request.form()

    uploads: dict[str, UploadFile | None] = {}
    for slot_key in budget_service.FILE_SLOTS:
        value = form.get(slot_key)
        # مقادیر خالی (کاربر چیزی انتخاب نکرده) گاهی رشته‌ی خالی می‌شوند؛ آن‌ها
        # معادل «فایلی ارسال نشده» در نظر گرفته می‌شوند.
        if isinstance(value, UploadFile) and value.filename:
            uploads[slot_key] = value
        else:
            uploads[slot_key] = None

    def _text(key: str) -> str | None:
        value = form.get(key)
        return str(value) if value is not None else None

    options = {
        "organization": _text("organization"),
        "base_year": _text("base_year"),
        "current_year": _text("current_year"),
        "meeting_context": _text("meeting_context"),
    }

    run = runs.start_run(session, project.id, SLUG)

    # تنظیمات هوش مصنوعی همین پروژه برای همین کارگاه (کلید/آدرس/مدل/پارامترها).
    # این فراخوانی در نشست همین درخواست انجام می‌شود و خروجی، یک شیء ساده‌ی
    # کاملاً پرشده است که به ترد پس‌زمینه پاس داده می‌شود؛ آن ترد هیچ دسترسی‌ای
    # به پایگاه‌داده ندارد. هر فیلد خالی کاربر همین‌جا بی‌سروصدا به پیش‌فرض
    # سامانه برمی‌گردد.
    llm_settings = budget_service.resolve_llm_settings(session, project.id)

    try:
        job_id = await budget_service.start_job(
            uploads,
            options,
            max_upload_bytes=settings.max_upload_bytes,
            project_id=project.id,
            run_id=run.id,
            on_finish=runs.make_finalizer(run.id),
            settings=llm_settings,
        )
    except budget_service.BudgetValidationError as exc:
        _discard_failed_run(session, run.id)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("unexpected error while starting budget-analysis job")
        _discard_failed_run(session, run.id)
        raise HTTPException(
            status_code=500,
            detail="خطای غیرمنتظره‌ای هنگام شروع پردازش رخ داد. لطفاً دوباره تلاش کنید.",
        ) from exc

    return StartJobResponse(job_id=job_id, run_id=run.id)


def _discard_failed_run(session: Session, run_id: int) -> None:
    """اگر اجرا هرگز شروع نشد، ردیف تاریخچه‌ی «در جریان» سرگردان باقی نمی‌ماند."""
    runs_repo.finish(
        session,
        run_id,
        status="error",
        result_summary={"error": "اجرا پیش از شروع پردازش متوقف شد."},
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_budget_job_status(
    job_id: str,
    project: Project = Depends(require_api_project),
) -> JobStatusResponse:
    job = get_job_or_404(job_id, project_id=project.id, workshop_slug=SLUG)
    return budget_service.build_status_response(job_id, job)


@router.get("/jobs/{job_id}/results", response_model=JobResultsResponse)
async def get_budget_job_results(
    job_id: str,
    project: Project = Depends(require_api_project),
) -> JobResultsResponse:
    job = get_job_or_404(job_id, project_id=project.id, workshop_slug=SLUG)
    if job.get("status") != "done":
        raise HTTPException(status_code=409, detail="پردازش هنوز به پایان نرسیده است.")
    return budget_service.build_results_response(job_id, job)


@router.get("/runs/{run_id}/download")
async def download_run_report(
    run_id: int,
    project: Project = Depends(require_api_project),
    session: Session = Depends(get_session),
) -> Response:
    """دانلود گزارش Word یک اجرا (چه در جریان، چه از تاریخچه)."""
    try:
        content, filename = runs.read_run_download(
            session, project.id, run_id, workshop_slug=SLUG
        )
    except (runs_repo.RunNotFound, runs.DownloadUnavailable) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return docx_response(content, filename, ascii_fallback_stem="budget-analysis-report")


# ---------------------------------------------------------------------------
# ثبت در رجیستری کارگاه‌ها
# ---------------------------------------------------------------------------
WORKSHOP = register(
    WorkshopDefinition(
        slug=SLUG,
        display_name_fa="تحلیل بودجه",
        description_fa=(
            "بارگذاری اصلاحیه بودجه تفصیلی سال پایه و سال جاری (Excel یا PDF چاپ‌شده از "
            "Excel) و تولید گزارش مدیریتی یکپارچه‌ی پایش بودجه: ماتریس انحرافات، "
            "یافته‌های بااهمیت، ریسک‌ها و موارد نیازمند تصمیم/شفاف‌سازی."
        ),
        icon="trending-up",
        accent="emerald",
        badge_fa="تحلیل مدیریتی",
        router=router,
        page_template="budget_analysis.html",
        page_renderer=render_page,
        estimate_progress=budget_service.estimate_progress,
        collect_result=budget_service.collect_result,
        has_download=budget_service.has_download,
        summary_chips_fa=budget_service.summary_chips_fa,
        highlights_fa=(
            "پذیرش فایل‌های Excel و PDF چاپ‌شده از Excel (تشخیص معنایی فرم‌ها، بدون وابستگی به موقعیت سلول)",
            "اجرای محورهای پایش (سقف عملکرد، حمایت فناوری، رشد حقوق، ساختار نیروی انسانی، مانده سنواتی، ثبات تشکیلات)",
            "ماتریس خطادهی ریزدانه با محل دقیق هر انحراف و اعلام صریح داده‌های موجود نیست",
            "گزارش مدیریتی Word راست‌به‌چپ با واحد مبالغ در سربرگ جدول‌ها و ترتیب بخش‌های مصوب",
        ),
        settings_keys=("api_key", "api_url", "model", "temperature", "max_output_tokens"),
        # تنظیمات اختصاصی این کارگاه واقعاً هنگام اجرا اعمال می‌شود (نگاه کنید به
        # ``budget_service.resolve_llm_settings`` در همین درخواست شروع اجرا)، و
        # دکمه‌ی «تست اتصال» فرم تنظیمات از همان کلاینت LLM این کارگاه استفاده
        # می‌کند.
        settings_applied=True,
        test_connection=budget_service.test_connection,
    )
)
