"""کارگاه «خلاصه‌سازی گزارش حسابرسی» به‌عنوان یک ورودی رجیستری.

ساختار دقیقاً موازی ``api/workshops/checklist.py`` است (که خودش نسخه‌ی
پروژه‌محورِ روتر قبلی ``api/routers/summary.py`` است):
endpoint ها زیر ``/api/projects/{project_id}/audit-summary/...`` قرار گرفته‌اند،
هر اجرا یک ردیف ``workshop_runs`` می‌سازد و نتیجه‌اش (خلاصه + فایل Word) در همان
ردیف ثبت می‌شود.

منطق پردازش دست‌نخورده است: ``api/services/summary_service.py`` همچنان
``audit_pipeline.py`` را بدون تغییر صدا می‌زند.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

# نکته: ``await request.form()`` همیشه نمونه‌هایی از
# ``starlette.datastructures.UploadFile`` برمی‌گرداند (نه زیرکلاس ``fastapi.UploadFile``)،
# دقیقاً مطابق ماژول کارگاه چک‌لیست از همین کلاس برای بررسی ``isinstance`` استفاده می‌شود.
from starlette.datastructures import UploadFile

from api.auth.deps import require_api_project
from api.config import get_settings
from api.db.base import get_session
from api.db.models import Project, User
from api.repositories import workshop_runs as runs_repo
from api.schemas.summary import JobResultsResponse, JobStatusResponse, StartJobResponse
from api.services import summary_service
from api.services.summary_service import AuditSummaryValidationError
from api.templating import templates
from api.utils.downloads import docx_response
from api.utils.jobs import get_job_or_404
from api.workshops import runs
from api.workshops.pages import workshop_page_context
from api.workshops.registry import WorkshopDefinition, register

logger = logging.getLogger(__name__)

SLUG = "audit-summary"

router = APIRouter(prefix=f"/api/projects/{{project_id}}/{SLUG}", tags=[SLUG])

# اسلات آپلود تکی این کارگاه -- با همان قالب دیکشنری که کامپوننت مشترک
# dropzone.html انتظار دارد (label/help/required/icon/types).
#
# تبصره: برخلاف چک‌لیست (که ``pipeline.FILE_SLOTS`` را به‌عنوان منبع واحد دارد و
# از طریق ``checklist_service.get_file_slots()`` مصرف می‌شود)، ``audit_pipeline.py``
# (خارج از محدوده‌ی این فاز) معادل ``FILE_SLOTS`` را تعریف نمی‌کند، به همین دلیل
# این دیکشنری این‌جا هاردکد شده است -- تنها استثنای شناخته‌شده به قاعده‌ی
# «منبع واحد تعریف اسلات‌ها». تنها مصرف‌کننده‌ی آن همین ماژول است (نه قالب)، پس
# افزودن کارگاه جدید هیچ تغییری این‌جا لازم ندارد.
AUDIT_REPORT_SLOT_KEY = "audit_report"
AUDIT_REPORT_SLOT = {
    "label": "گزارش حسابرسی",
    "help": "گزارش حسابرسی موجود برای خلاصه‌سازی با کمک هوش مصنوعی",
    "required": True,
    "icon": "file-text",
    "types": ["pdf", "doc", "docx"],
}


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
            slot_key=AUDIT_REPORT_SLOT_KEY,
            slot=AUDIT_REPORT_SLOT,
        ),
    )


# ---------------------------------------------------------------------------
# API کارگاه
# ---------------------------------------------------------------------------
@router.post("/jobs", response_model=StartJobResponse)
async def create_summary_job(
    request: Request,
    project: Project = Depends(require_api_project),
    session: Session = Depends(get_session),
) -> StartJobResponse:
    """شروع یک اجرای جدید خلاصه‌سازی برای این پروژه.

    فرم شامل یک فایل تکی (کلید ``audit_report``) و دو فیلد متنی اختیاری
    (``organization``، ``meeting_context``) است.
    """
    settings = get_settings()
    form = await request.form()

    upload = form.get(AUDIT_REPORT_SLOT_KEY)
    if not isinstance(upload, UploadFile) or not upload.filename:
        upload = None

    organization = form.get("organization")
    meeting_context = form.get("meeting_context")

    run = runs.start_run(session, project.id, SLUG)

    try:
        job_id = await summary_service.start_job(
            upload,
            str(organization) if organization is not None else None,
            str(meeting_context) if meeting_context is not None else None,
            max_upload_bytes=settings.max_upload_bytes,
            project_id=project.id,
            run_id=run.id,
            on_finish=runs.make_finalizer(run.id),
        )
    except AuditSummaryValidationError as exc:
        _discard_failed_run(session, run.id)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("unexpected error while starting audit-summary job")
        _discard_failed_run(session, run.id)
        raise HTTPException(
            status_code=500,
            detail="خطای غیرمنتظره‌ای هنگام شروع پردازش رخ داد. لطفاً دوباره تلاش کنید.",
        ) from exc

    return StartJobResponse(job_id=job_id, run_id=run.id)


def _discard_failed_run(session: Session, run_id: int) -> None:
    """اگر اجرا هرگز شروع نشد، ردیف تاریخچه‌ی «در جریان» باقی نمی‌ماند."""
    runs_repo.finish(
        session,
        run_id,
        status="error",
        result_summary={"error": "اجرا پیش از شروع پردازش متوقف شد."},
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_summary_job_status(
    job_id: str,
    project: Project = Depends(require_api_project),
) -> JobStatusResponse:
    job = get_job_or_404(job_id, project_id=project.id, workshop_slug=SLUG)
    return summary_service.build_status_response(job_id, job)


@router.get("/jobs/{job_id}/results", response_model=JobResultsResponse)
async def get_summary_job_results(
    job_id: str,
    project: Project = Depends(require_api_project),
) -> JobResultsResponse:
    job = get_job_or_404(job_id, project_id=project.id, workshop_slug=SLUG)
    if job.get("status") != "done":
        raise HTTPException(status_code=409, detail="پردازش هنوز به پایان نرسیده است.")
    return summary_service.build_results_response(job_id, job)


@router.get("/runs/{run_id}/download")
async def download_run_summary(
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
    return docx_response(content, filename, ascii_fallback_stem="summary")


# ---------------------------------------------------------------------------
# ثبت در رجیستری کارگاه‌ها
# ---------------------------------------------------------------------------
WORKSHOP = register(
    WorkshopDefinition(
        slug=SLUG,
        display_name_fa="خلاصه‌سازی گزارش حسابرسی",
        description_fa=(
            "بارگذاری یک گزارش حسابرسی موجود (PDF/DOC/DOCX) و تبدیل آن به خلاصه‌ای "
            "روان، ساختارمند و آماده‌ی ارائه در جلسه با کمک هوش مصنوعی."
        ),
        icon="sparkles",
        accent="navy",
        badge_fa="هوش مصنوعی",
        router=router,
        page_template="audit_summary.html",
        page_renderer=render_page,
        estimate_progress=summary_service.estimate_progress,
        collect_result=summary_service.collect_result,
        has_download=summary_service.has_download,
        summary_chips_fa=summary_service.summary_chips_fa,
        highlights_fa=(
            "پذیرش گزارش‌های PDF، DOC و DOCX",
            "استخراج متن و آماده‌سازی محتوا برای پردازش زبانی",
            "خلاصه‌سازی هوشمند با ساختار بخش‌بندی‌شده و قابل‌ویرایش",
            "خروجی Word با سربرگ سازمانی و امکان افزودن توضیح جلسه",
        ),
        settings_keys=("api_key", "api_url", "model"),
        # تنظیمات اختصاصی فعال نیست (settings_applied پیش‌فرض False است):
        # ``audit_pipeline.run_audit_summary`` پیکربندی مدل را خودش و درون
        # ``audit_pipeline.py`` با ``LLMConfig(stream=False)`` می‌سازد و هیچ
        # پارامتری برای تزریق کلید/آدرس/مدل نمی‌پذیرد. اتصال این کارگاه به
        # resolver تنظیمات یعنی بازنویسی آن ماژول -- که خارج از محدوده‌ی این
        # فاز است. بنابراین فرم تنظیمات برای این کارگاه نمایش داده نمی‌شود تا
        # وعده‌ی بی‌اثر به کاربر داده نشود.
    )
)
