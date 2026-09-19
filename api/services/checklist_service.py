"""
لایه‌ی سرویس کارگاه چک‌لیست حسابرسی.

این ماژول هیچ منطق حسابرسی/استخراج جدیدی ندارد -- صرفاً یک لایه‌ی نازک روی
``pipeline.py`` است که:
  ۱) فایل‌های آپلودی FastAPI (``UploadFile``) را اعتبارسنجی و روی دیسک ذخیره
     می‌کند (با فراخوانی‌ی بدون تغییر ``pipeline.save_uploaded_file`` /
     ``pipeline.save_report_upload`` از طریق آداپتور ``InMemoryUploadAdapter``).
  ۲) یک job در ``job_manager`` عمومی می‌سازد و پردازش پس‌زمینه‌ی موجود
     پایپلاین (``pipeline.start_checklist_job``، که خودش یک ترد جدا اجرا
     می‌کند) را روی آن راه‌اندازی می‌کند.
  ۳) دیکشنری خام job را به مدل‌های Pydantic قابل نمایش در API/HTML تبدیل
     می‌کند.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable, Optional

# نکته: ``await request.form()`` در لایه‌ی روتر همیشه نمونه‌هایی از
# ``starlette.datastructures.UploadFile`` برمی‌گرداند (نه زیرکلاس آن
# ``fastapi.UploadFile``)، به همین دلیل اینجا هم از همان نوع برای کامل بودن
# type hint استفاده می‌شود.
from starlette.datastructures import UploadFile

import pipeline
from api.jobs.job_manager import JobManager, job_manager
from api.schemas.checklist import (
    ChecklistItem,
    JobResultsResponse,
    JobStatusResponse,
    JobSummary,
)
from api.services import checklist_kb_service
from api.utils.uploads import InMemoryUploadAdapter
from api.workshops.registry import ResultArtifact

logger = logging.getLogger(__name__)


class ChecklistValidationError(Exception):
    """خطای اعتبارسنجی سطح-درخواست (فایل الزامی گم است، پسوند/حجم نامعتبر است و...).

    پیام این خطا از قبل به فارسی و قابل‌نمایش مستقیم به کاربر است.
    """


def get_file_slots() -> dict[str, dict[str, Any]]:
    """برای رندر داینامیک اسلات‌های آپلود در قالب Jinja2 (به‌جای هاردکد کردن)."""
    return pipeline.FILE_SLOTS


def _validate_upload(slot_key: str, upload: UploadFile, max_bytes: int, content: bytes) -> None:
    slot = pipeline.FILE_SLOTS[slot_key]
    suffix = Path(upload.filename or "").suffix.lower().lstrip(".")
    allowed = [t.lower() for t in slot.get("types", [])]
    if suffix not in allowed:
        raise ChecklistValidationError(
            f"فرمت فایل «{upload.filename}» برای «{slot['label']}» پذیرفته نیست. "
            f"فرمت‌های مجاز: {', '.join(allowed)}."
        )
    if len(content) > max_bytes:
        max_mb = max_bytes / (1024 * 1024)
        raise ChecklistValidationError(
            f"حجم فایل «{upload.filename}» بیش از حد مجاز ({max_mb:.0f} مگابایت) است."
        )
    if len(content) == 0:
        raise ChecklistValidationError(f"فایل «{upload.filename}» خالی است.")


async def start_job(
    uploads: dict[str, Optional[UploadFile]],
    entity_name: Optional[str],
    max_upload_bytes: int,
    *,
    project_id: int,
    run_id: int,
    user_id: Optional[int] = None,
    on_finish: Optional[Callable[[dict[str, Any]], None]] = None,
    manager: JobManager = job_manager,
) -> str:
    """اعتبارسنجی و ذخیره‌ی فایل‌های آپلودی، سپس شروع پردازش پس‌زمینه‌ی چک‌لیست.

    این تابع فقط بخش‌های سریع (اعتبارسنجی + ذخیره‌ی فایل روی دیسک) را به‌صورت
    همزمان (await) اجرا می‌کند؛ پردازش سنگین (استخراج + چک‌لیست + گزارش
    کمیسیون) توسط ``pipeline.start_checklist_job`` در یک ترد پس‌زمینه‌ی جدا
    انجام می‌شود و این تابع بلافاصله پس از شروع آن ترد برمی‌گردد.

    ``project_id``/``run_id`` صرفاً برچسب‌های job هستند (تا چند کارگاه در چند
    پروژه بدون تداخل هم‌زمان اجرا شوند) و ``on_finish`` پس از پایان کار، ردیف
    تاریخچه‌ی ``workshop_runs`` را ثبت می‌کند.
    """
    required_keys = [k for k, v in pipeline.FILE_SLOTS.items() if v["required"]]
    missing = [
        pipeline.FILE_SLOTS[k]["label"]
        for k in required_keys
        if uploads.get(k) is None
    ]
    if missing:
        raise ChecklistValidationError(
            "لطفاً فایل‌های الزامی زیر را بارگذاری کنید: " + "، ".join(missing)
        )

    workdir = pipeline.make_temp_workdir()
    try:
        file_paths: dict[str, Optional[Path]] = {}
        audit_report_path: Optional[Path] = None

        for slot_key, upload in uploads.items():
            if slot_key == "audit_report_doc":
                # این اسلات جدا از file_paths نگه داشته می‌شود -- دقیقاً مطابق
                # الگوی legacy_streamlit/app.py (پارامتر مجزای audit_report_path
                # در pipeline.run_full_pipeline).
                if upload is not None:
                    content = await upload.read()
                    _validate_upload(slot_key, upload, max_upload_bytes, content)
                    adapter = InMemoryUploadAdapter(upload.filename or "audit_report.bin", content)
                    audit_report_path = pipeline.save_report_upload(adapter, workdir)
                continue

            if upload is None:
                file_paths[slot_key] = None
                continue

            content = await upload.read()
            _validate_upload(slot_key, upload, max_upload_bytes, content)
            adapter = InMemoryUploadAdapter(upload.filename or f"{slot_key}.bin", content)
            file_paths[slot_key] = pipeline.save_uploaded_file(adapter, workdir)
    except ChecklistValidationError:
        pipeline.cleanup_workdir(workdir)
        raise
    except pipeline.PipelineError as exc:
        pipeline.cleanup_workdir(workdir)
        raise ChecklistValidationError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        pipeline.cleanup_workdir(workdir)
        logger.exception("unexpected error while saving checklist uploads")
        raise ChecklistValidationError(
            "خطای غیرمنتظره‌ای هنگام آماده‌سازی فایل‌های بارگذاری‌شده رخ داد."
        ) from exc

    job_id, job = manager.create(
        pipeline.new_checklist_job,
        workdir=workdir,
        kind="checklist",
        project_id=project_id,
        run_id=run_id,
        on_finish=on_finish,
    )
    # نام سازمان در متادیتای نتیجه‌ی اجرا (ردیف تاریخچه) نگه داشته می‌شود؛
    # ``run_full_pipeline`` خروجی خودش این مقدار را برنمی‌گرداند و این کلید
    # صرفاً یک برچسب خصوصی روی دیکشنری job است (پایپ‌لاین آن را نمی‌خواند).
    job["_entity_name"] = (entity_name or "").strip() or None

    # شروع فوری و موازی ایندکس‌سازی پایگاه‌دانش این اجرا -- در ترد جداگانه
    # خودش (``checklist_kb_service.start_indexing``)، بدون این‌که پایپلاین
    # چک‌لیست کندتر یا منتظر آن بماند. اگر ``user_id`` در دسترس نباشد (مثلاً
    # فراخوانی قدیمی‌تر تست‌ها)، این قابلیت به‌طور کامل رد می‌شود و رفتار
    # قبلی کارگاه بدون تغییر باقی می‌ماند.
    resolve_false_questions = None
    if user_id is not None:
        kb_join = checklist_kb_service.start_indexing(
            user_id=user_id,
            project_id=project_id,
            file_paths=file_paths,
        )
        resolve_false_questions = kb_join.resolve_false_questions

    pipeline.start_checklist_job(
        job,
        file_paths,
        workdir,
        audit_report_path=audit_report_path,
        entity_name=(entity_name or "").strip() or None,
        resolve_false_questions=resolve_false_questions,
    )
    manager.watch_lifecycle(job_id, job)
    logger.info(
        "checklist job %s started (project=%s, run=%s, entity_name=%r)",
        job_id,
        project_id,
        run_id,
        entity_name,
    )
    return job_id


# ---------------------------------------------------------------------------
# تخمین درصد پیشرفت از روی متن مرحله‌ی جاری (job["stage"])
# ---------------------------------------------------------------------------
_QUESTION_PROGRESS_RE = re.compile(r"سوال\s+(\d+)\s+از\s+(\d+)")


def estimate_progress(job: dict[str, Any]) -> int:
    """درصد پیشرفت تقریبی از روی مرحله‌ی جاری job.

    عمومی (public) است چون هم پاسخ وضعیت همین کارگاه و هم پنل «کارهای در جریان»
    پروژه (که همه‌ی کارگاه‌ها را نشان می‌دهد) از آن استفاده می‌کنند؛ رجیستری
    کارگاه‌ها این تابع را به‌عنوان تخمین‌زننده‌ی پیشرفت همین کارگاه ثبت می‌کند.
    """
    status = job.get("status")
    if status == "done":
        return 100
    if status == "pending":
        return 0

    stage = job.get("stage") or ""

    match = _QUESTION_PROGRESS_RE.search(stage)
    if match:
        current, total = int(match.group(1)), int(match.group(2))
        ratio = current / total if total else 0.0
        return int(20 + ratio * 55)  # اجرای چک‌لیست بازه‌ی ۲۰٪ تا ۷۵٪ را پر می‌کند

    if "در حال شروع" in stage:
        return 2
    if "استخراج فایل‌های ورودی" in stage:
        return 10
    if "استخراج متن از فایل گزارش حسابرسی" in stage:
        return 78
    if "تولید گزارش کمیسیون" in stage:
        return 85
    if "با موفقیت ساخته شد" in stage:
        return 97
    if "پردازش با موفقیت به اتمام رسید" in stage:
        return 100

    # درصد پیش‌فرض محافظه‌کارانه بر اساس تعداد پیام‌های لاگ ثبت‌شده تا این لحظه
    return min(95, 5 + 3 * len(job.get("logs", [])))


def build_status_response(job_id: str, job: dict[str, Any]) -> JobStatusResponse:
    status = job.get("status", "pending")
    summary = None
    report_ready = False
    elapsed_seconds = None

    if status == "done":
        result = job.get("result") or {}
        summary_dict = result.get("summary")
        if summary_dict:
            summary = JobSummary(**summary_dict)
        committee_report = result.get("committee_report") or {}
        report_ready = bool(committee_report.get("docx_bytes"))
        elapsed_seconds = result.get("elapsed_seconds")

    return JobStatusResponse(
        job_id=job_id,
        run_id=job.get("_run_id"),
        status=status,
        stage=job.get("stage", ""),
        progress=estimate_progress(job),
        logs=job.get("logs", [])[-20:],
        error=job.get("error"),
        summary=summary,
        report_ready=report_ready,
        elapsed_seconds=elapsed_seconds,
    )


def build_results_response(job_id: str, job: dict[str, Any]) -> JobResultsResponse:
    result = job.get("result") or {}
    summary_dict = result.get("summary") or {
        "total": 0,
        "true_count": 0,
        "false_count": 0,
        "error_count": 0,
        "manual_count": 0,
        "compliance_rate": 0.0,
    }
    committee_report = result.get("committee_report") or {}
    items = [ChecklistItem(**item) for item in result.get("checklist_results", [])]
    return JobResultsResponse(
        job_id=job_id,
        summary=JobSummary(**summary_dict),
        warnings=result.get("warnings", []),
        items=items,
        report_ready=bool(committee_report.get("docx_bytes")),
        committee_report_error=committee_report.get("error"),
        used_audit_report_text=bool(committee_report.get("used_audit_report_text")),
    )


def get_report_bytes(job: dict[str, Any]) -> tuple[bytes, str]:
    result = job.get("result") or {}
    committee_report = result.get("committee_report") or {}
    docx_bytes = committee_report.get("docx_bytes")
    if not docx_bytes:
        raise ChecklistValidationError("گزارش کمیسیون برای این پردازش در دسترس نیست.")
    filename = committee_report.get("docx_filename") or "گزارش_کمیسیون.docx"
    return docx_bytes, filename


# ---------------------------------------------------------------------------
# ثبت تاریخچه: تبدیل job تمام‌شده به artifact ماندگار
# ---------------------------------------------------------------------------
def has_download(job: dict[str, Any]) -> bool:
    """آیا همین حالا فایل نتیجه‌ی این job قابل دانلود است؟"""
    result = job.get("result") or {}
    return bool((result.get("committee_report") or {}).get("docx_bytes"))


def summary_chips_fa(summary: dict[str, Any]) -> list[str]:
    """برچسب‌های کوتاه خلاصه‌ی نتیجه‌ی چک‌لیست برای فهرست تاریخچه."""
    from api.utils.formatting import fa_number, fa_percent

    chips: list[str] = []
    if summary.get("entity_name"):
        chips.append(str(summary["entity_name"]))
    total = summary.get("total")
    if total is not None:
        chips.append(
            f"{fa_number(summary.get('false_count', 0))} مورد نامنطبق از {fa_number(total)} بررسی"
        )
    if summary.get("compliance_rate") is not None:
        chips.append(f"درصد تطابق: {fa_percent(summary.get('compliance_rate'))}")
    if summary.get("used_audit_report_text"):
        chips.append("با استفاده از متن گزارش حسابرسی")
    if summary.get("report_error"):
        chips.append("گزارش کمیسیون تولید نشد")
    return chips


def collect_result(job: dict[str, Any]) -> ResultArtifact:
    """خروجی نهایی اجرای چک‌لیست را برای ذخیره در تاریخچه آماده می‌کند.

    فقط *نتیجه* ذخیره می‌شود: فایل Word گزارش کمیسیون + متادیتای شمارشی نتیجه.
    هیچ محتوایی از فایل‌های ورودی (بودجه/صورت‌های مالی/ترازنامه) -- نه محتوا و نه
    مسیر -- این‌جا نگه داشته نمی‌شود؛ فایل‌های ورودی همان‌طور که قبلاً هم بود، پس از
    پایان پردازش توسط ``pipeline.cleanup_workdir`` حذف می‌شوند.
    """
    result = job.get("result") or {}
    summary = dict(result.get("summary") or {})
    committee_report = result.get("committee_report") or {}

    summary.update(
        {
            "warnings": list(result.get("warnings") or [])[:10],
            "used_audit_report_text": bool(committee_report.get("used_audit_report_text")),
            "elapsed_seconds": result.get("elapsed_seconds"),
            "entity_name": job.get("_entity_name"),
        }
    )

    filename = committee_report.get("docx_filename") or "گزارش_کمیسیون.docx"
    content = committee_report.get("docx_bytes")
    if content:
        summary["result_filename"] = filename
    elif committee_report.get("error"):
        # خودِ اجرای چک‌لیست موفق بوده اما تولید گزارش کمیسیون شکست خورده است (مثلاً
        # خطای سرویس هوش مصنوعی) -- این تفاوت باید در تاریخچه دیده شود، وگرنه ردیف
        # بدون فایل نتیجه، شبیه یک اجرای ناقصِ مبهم به نظر می‌رسد.
        summary["report_error"] = committee_report["error"]

    return ResultArtifact(summary=summary, filename=filename, content=content)
