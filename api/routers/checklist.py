"""
Endpoint های API کارگاه چک‌لیست حسابرسی (``/api/checklist/*``).

این روتر عمداً «نازک» نگه داشته شده: صرفاً درخواست HTTP را می‌خواند، به
``api.services.checklist_service`` (که خودش ``pipeline.py`` را بدون تغییر
صدا می‌زند) پاس می‌دهد و پاسخ JSON برمی‌گرداند. هیچ منطق پردازش/حسابرسی این‌جا
نیست.
"""
from __future__ import annotations

import logging

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

# نکته: ``await request.form()`` همیشه نمونه‌هایی از ``starlette.datastructures.
# UploadFile`` برمی‌گرداند (نه زیرکلاس ``fastapi.UploadFile``)، بنابراین برای
# بررسی ``isinstance`` باید همین کلاس پایه ایمپورت شود؛ وگرنه بررسی همیشه
# نادرست ارزیابی می‌شود و آپلود فایل بی‌صدا شکست می‌خورد.
from starlette.datastructures import UploadFile

import pipeline
from api.config import get_settings
from api.schemas.checklist import JobResultsResponse, JobStatusResponse, StartJobResponse
from api.services import checklist_service
from api.services.checklist_service import ChecklistValidationError
from api.utils.jobs import get_job_or_404

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/checklist", tags=["checklist"])


@router.post("/jobs", response_model=StartJobResponse)
async def create_checklist_job(request: Request) -> StartJobResponse:
    """شروع یک اجرای جدید پایپلاین چک‌لیست حسابرسی.

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

    try:
        job_id = await checklist_service.start_job(
            uploads,
            entity_name,
            max_upload_bytes=settings.max_upload_bytes,
        )
    except ChecklistValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("unexpected error while starting checklist job")
        raise HTTPException(
            status_code=500,
            detail="خطای غیرمنتظره‌ای هنگام شروع پردازش رخ داد. لطفاً دوباره تلاش کنید.",
        ) from exc

    return StartJobResponse(job_id=job_id)


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_checklist_job_status(job_id: str) -> JobStatusResponse:
    job = get_job_or_404(job_id)
    return checklist_service.build_status_response(job_id, job)


@router.get("/jobs/{job_id}/results", response_model=JobResultsResponse)
async def get_checklist_job_results(job_id: str) -> JobResultsResponse:
    job = get_job_or_404(job_id)
    if job.get("status") != "done":
        raise HTTPException(status_code=409, detail="پردازش هنوز به پایان نرسیده است.")
    return checklist_service.build_results_response(job_id, job)


@router.get("/jobs/{job_id}/report")
async def download_checklist_report(job_id: str) -> Response:
    job = get_job_or_404(job_id)
    if job.get("status") != "done":
        raise HTTPException(status_code=409, detail="پردازش هنوز به پایان نرسیده است.")
    try:
        docx_bytes, filename = checklist_service.get_report_bytes(job)
    except ChecklistValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # نام فایل فارسی است و هدر HTTP فقط latin-1 را می‌پذیرد، به همین دلیل
    # از کدگذاری RFC 5987 (filename*=UTF-8''...) همراه یک نام fallback
    # ساده‌ی ASCII استفاده می‌کنیم تا مرورگرهای قدیمی‌تر هم کار کنند.
    ascii_fallback = Path(filename).suffix or ".docx"
    encoded_filename = quote(filename)
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": (
                f'attachment; filename="report{ascii_fallback}"; '
                f"filename*=UTF-8''{encoded_filename}"
            ),
        },
    )
