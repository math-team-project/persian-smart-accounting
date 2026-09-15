"""
Endpoint های API کارگاه خلاصه‌سازی گزارش حسابرسی (``/api/summary/*``).

این روتر عمداً «نازک» نگه داشته شده، دقیقاً مطابق الگوی
``api/routers/checklist.py``: صرفاً درخواست HTTP را می‌خواند، به
``api.services.summary_service`` (که خودش ``audit_pipeline.py`` را بدون
تغییر صدا می‌زند) پاس می‌دهد و پاسخ JSON برمی‌گرداند. هیچ منطق
استخراج/خلاصه‌سازی این‌جا نیست.
"""
from __future__ import annotations

import logging

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

# نکته: ``await request.form()`` همیشه نمونه‌هایی از ``starlette.datastructures.
# UploadFile`` برمی‌گرداند (نه زیرکلاس ``fastapi.UploadFile``)، دقیقاً مطابق
# checklist.py از همین کلاس برای بررسی ``isinstance`` استفاده می‌شود.
from starlette.datastructures import UploadFile

from api.config import get_settings
from api.schemas.summary import JobResultsResponse, JobStatusResponse, StartJobResponse
from api.services import summary_service
from api.services.summary_service import AuditSummaryValidationError
from api.utils.jobs import get_job_or_404

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/summary", tags=["audit-summary"])


@router.post("/jobs", response_model=StartJobResponse)
async def create_summary_job(request: Request) -> StartJobResponse:
    """شروع یک اجرای جدید پایپلاین خلاصه‌سازی گزارش حسابرسی.

    فرم شامل یک فایل تکی (کلید ``audit_report``) و دو فیلد متنی اختیاری
    (``organization``، ``meeting_context``) است.
    """
    settings = get_settings()
    form = await request.form()

    upload = form.get("audit_report")
    if not isinstance(upload, UploadFile) or not upload.filename:
        upload = None

    organization = form.get("organization")
    meeting_context = form.get("meeting_context")

    try:
        job_id = await summary_service.start_job(
            upload,
            str(organization) if organization is not None else None,
            str(meeting_context) if meeting_context is not None else None,
            max_upload_bytes=settings.max_upload_bytes,
        )
    except AuditSummaryValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("unexpected error while starting audit-summary job")
        raise HTTPException(
            status_code=500,
            detail="خطای غیرمنتظره‌ای هنگام شروع پردازش رخ داد. لطفاً دوباره تلاش کنید.",
        ) from exc

    return StartJobResponse(job_id=job_id)


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_summary_job_status(job_id: str) -> JobStatusResponse:
    job = get_job_or_404(job_id)
    return summary_service.build_status_response(job_id, job)


@router.get("/jobs/{job_id}/results", response_model=JobResultsResponse)
async def get_summary_job_results(job_id: str) -> JobResultsResponse:
    job = get_job_or_404(job_id)
    if job.get("status") != "done":
        raise HTTPException(status_code=409, detail="پردازش هنوز به پایان نرسیده است.")
    return summary_service.build_results_response(job_id, job)


@router.get("/jobs/{job_id}/download")
async def download_summary_report(job_id: str) -> Response:
    job = get_job_or_404(job_id)
    if job.get("status") != "done":
        raise HTTPException(status_code=409, detail="پردازش هنوز به پایان نرسیده است.")
    try:
        docx_bytes, filename = summary_service.get_report_bytes(job)
    except AuditSummaryValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # نام فایل فارسی است و هدر HTTP فقط latin-1 را می‌پذیرد، دقیقاً مطابق
    # checklist.py از کدگذاری RFC 5987 استفاده می‌کنیم.
    ascii_fallback = Path(filename).suffix or ".docx"
    encoded_filename = quote(filename)
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": (
                f'attachment; filename="summary{ascii_fallback}"; '
                f"filename*=UTF-8''{encoded_filename}"
            ),
        },
    )
