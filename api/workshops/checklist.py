"""کارگاه «بررسی چک‌لیست حسابرسی» به‌عنوان یک ورودی رجیستری.

این ماژول همان روتر کارگاه چک‌لیست است (که پیش‌تر در ``api/routers/checklist.py``
بود) با دو تفاوت ساختاری:

۱) همه‌ی endpoint ها زیر یک پروژه تعریف شده‌اند:
   ``/api/projects/{project_id}/checklist/...`` -- بنابراین یک اجرا همیشه به
   پروژه‌ی خودش گره خورده است و دو اجرای هم‌زمان (در یک پروژه یا در دو پروژه)
   هیچ حالت مشترکی ندارند.
۲) هر اجرا پیش از شروع پردازش یک ردیف ``workshop_runs`` می‌سازد و پس از پایان
   کار، نتیجه (فایل Word گزارش کمیسیون + متادیتای شمارشی) در همان ردیف ثبت
   می‌شود -- این «تاریخچه‌ی پروژه» است.

منطق پردازش هیچ تغییری نکرده است: ``api/services/checklist_service.py`` همچنان
``pipeline.py`` را بدون دست‌زدن صدا می‌زند.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

# نکته: ``await request.form()`` همیشه نمونه‌هایی از ``starlette.datastructures.
# UploadFile`` برمی‌گرداند (نه زیرکلاس ``fastapi.UploadFile``)، بنابراین برای
# بررسی ``isinstance`` باید همین کلاس پایه ایمپورت شود؛ وگرنه بررسی همیشه
# نادرست ارزیابی می‌شود و آپلود فایل بی‌صدا شکست می‌خورد.
from starlette.datastructures import UploadFile

import pipeline
from api.auth.deps import require_api_project
from api.config import get_settings
from api.db.base import get_session
from api.db.models import Project, User
from api.repositories import workshop_runs as runs_repo
from api.schemas.checklist import JobResultsResponse, JobStatusResponse, StartJobResponse
from api.services import checklist_service
from api.services.checklist_service import ChecklistValidationError
from api.templating import templates
from api.utils.downloads import docx_response
from api.utils.jobs import get_job_or_404
from api.workshops import runs
from api.workshops.pages import workshop_page_context
from api.workshops.registry import WorkshopDefinition, register

logger = logging.getLogger(__name__)

SLUG = "checklist"

router = APIRouter(prefix=f"/api/projects/{{project_id}}/{SLUG}", tags=[SLUG])


# ---------------------------------------------------------------------------
# صفحه‌ی HTML کارگاه (داخل یک پروژه)
# ---------------------------------------------------------------------------
def render_page(request: Request, project: Project, user: User) -> HTMLResponse:
    file_slots = checklist_service.get_file_slots()
    required_slots = {k: v for k, v in file_slots.items() if v["required"]}
    optional_slots = {k: v for k, v in file_slots.items() if not v["required"]}
    return templates.TemplateResponse(
        request,
        WORKSHOP.page_template,
        workshop_page_context(
            project,
            user,
            WORKSHOP,
            required_slots=required_slots,
            optional_slots=optional_slots,
        ),
    )


@router.post("/jobs", response_model=StartJobResponse)
async def create_checklist_job(
    request: Request,
    project: Project = Depends(require_api_project),
    session: Session = Depends(get_session),
) -> StartJobResponse:
    """شروع یک اجرای جدید چک‌لیست برای این پروژه.

    فیلدهای فرم به‌صورت داینامیک بر اساس ``pipeline.FILE_SLOTS`` خوانده
    می‌شوند (نام هر فایل باید برابر با کلید اسلات باشد، مثلاً
    ``revised_budget``) تا افزودن یک اسلات جدید به pipeline.py نیازی به
    تغییر این روتر نداشته باشد.
    """
    settings = get_settings()
    form = await request.form()

    uploads: dict[str, UploadFile | None] = {}
    for slot_key in pipeline.FILE_SLOTS:
        value = form.get(slot_key)
        # مقادیر خالی (کاربر چیزی انتخاب نکرده) توسط مرورگر گاهی به‌صورت رشته‌ی
        # خالی ارسال می‌شوند؛ آن‌ها را معادل «فایلی ارسال نشده» در نظر می‌گیریم.
        if isinstance(value, UploadFile) and value.filename:
            uploads[slot_key] = value
        else:
            uploads[slot_key] = None

    entity_name = form.get("entity_name")
    entity_name = str(entity_name) if entity_name is not None else None

    # ردیف تاریخچه پیش از شروع پردازش ساخته می‌شود تا اجرا بلافاصله در پنل
    # «کارهای در جریان» دیده شود.
    run = runs.start_run(session, project.id, SLUG)

    try:
        job_id = await checklist_service.start_job(
            uploads,
            entity_name,
            max_upload_bytes=settings.max_upload_bytes,
            project_id=project.id,
            run_id=run.id,
            on_finish=runs.make_finalizer(run.id),
        )
    except ChecklistValidationError as exc:
        _discard_failed_run(session, run.id)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("unexpected error while starting checklist job")
        _discard_failed_run(session, run.id)
        raise HTTPException(
            status_code=500,
            detail="خطای غیرمنتظره‌ای هنگام شروع پردازش رخ داد. لطفاً دوباره تلاش کنید.",
        ) from exc

    return StartJobResponse(job_id=job_id, run_id=run.id)


def _discard_failed_run(session: Session, run_id: int) -> None:
    """اگر اجرا هرگز شروع نشد، ردیف تاریخچه‌ی خالی باقی نمی‌ماند.

    (در اعتبارسنجی ناموفق، job ای وجود ندارد که callback پایان کار را صدا بزند.)
    """
    runs_repo.finish(
        session,
        run_id,
        status="error",
        result_summary={"error": "اجرا پیش از شروع پردازش متوقف شد."},
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_checklist_job_status(
    job_id: str,
    project: Project = Depends(require_api_project),
) -> JobStatusResponse:
    job = get_job_or_404(job_id, project_id=project.id, workshop_slug=SLUG)
    return checklist_service.build_status_response(job_id, job)


@router.get("/jobs/{job_id}/results", response_model=JobResultsResponse)
async def get_checklist_job_results(
    job_id: str,
    project: Project = Depends(require_api_project),
) -> JobResultsResponse:
    job = get_job_or_404(job_id, project_id=project.id, workshop_slug=SLUG)
    if job.get("status") != "done":
        raise HTTPException(status_code=409, detail="پردازش هنوز به پایان نرسیده است.")
    return checklist_service.build_results_response(job_id, job)


@router.get("/runs/{run_id}/download")
async def download_run_report(
    run_id: int,
    project: Project = Depends(require_api_project),
    session: Session = Depends(get_session),
) -> Response:
    """دانلود فایل نتیجه‌ی یک اجرا (چه در جریان، چه از تاریخچه)."""
    try:
        content, filename = runs.read_run_download(
            session, project.id, run_id, workshop_slug=SLUG
        )
    except (runs_repo.RunNotFound, runs.DownloadUnavailable) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return docx_response(content, filename, ascii_fallback_stem="report")


# ---------------------------------------------------------------------------
# ثبت در رجیستری کارگاه‌ها
# ---------------------------------------------------------------------------
WORKSHOP = register(
    WorkshopDefinition(
        slug=SLUG,
        display_name_fa="بررسی چک‌لیست حسابرسی مالی",
        description_fa=(
            "بارگذاری فایل‌های بودجه، صورت‌های مالی و ترازنامه و ارزیابی خودکار "
            "پرسش‌های چک‌لیست؛ موارد نامنطبق مشخص و گزارش کمیسیون در قالب Word تولید می‌شود."
        ),
        icon="clipboard-check",
        accent="teal",
        badge_fa="تحلیل کمی",
        router=router,
        page_template="checklist.html",
        page_renderer=render_page,
        estimate_progress=checklist_service.estimate_progress,
        collect_result=checklist_service.collect_result,
        has_download=checklist_service.has_download,
        summary_chips_fa=checklist_service.summary_chips_fa,
        highlights_fa=(
            "پذیرش فایل‌های Excel و PDF (بودجه، صورت‌های مالی، ترازنامه و اسناد اختیاری)",
            "ارزیابی خودکار پرسش‌های چک‌لیست و استخراج مقادیر از اسناد",
            "تفکیک موارد منطبق، نامنطبق، خطای پردازش و نیازمند بررسی دستی",
            "تولید گزارش کمیسیون (Word) به‌همراه درصد تطابق و فهرست مغایرت‌ها",
        ),
        settings_keys=("api_key", "api_url", "model"),
        # تنظیمات اختصاصی فعال نیست (settings_applied پیش‌فرض False است):
        # ``pipeline.run_full_pipeline`` کلید و آدرس و مدل را مستقیم از محیط
        # می‌خواند و داخل ``pipeline.py`` یک ``LLMConfig`` می‌سازد؛ تزریق
        # تنظیمات پروژه یعنی بازنویسی آن ماژول -- که خارج از محدوده‌ی این فاز
        # است. بنابراین فرم تنظیمات برای این کارگاه نمایش داده نمی‌شود.
    )
)
