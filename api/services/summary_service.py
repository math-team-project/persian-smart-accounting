"""
لایه‌ی سرویس کارگاه خلاصه‌سازی گزارش حسابرسی.

این ماژول هیچ منطق استخراج/خلاصه‌سازی جدیدی ندارد -- صرفاً یک لایه‌ی نازک روی
``audit_pipeline.py`` است، دقیقاً به همان الگوی ``api/services/checklist_service.py``:
  ۱) فایل آپلودی FastAPI (``UploadFile``) را اعتبارسنجی و روی دیسک ذخیره
     می‌کند (با فراخوانی‌ی بدون تغییر ``audit_pipeline.save_audit_upload`` از
     طریق آداپتور مشترک ``InMemoryUploadAdapter``).
  ۲) یک job در ``job_manager`` عمومی می‌سازد و پردازش پس‌زمینه‌ی موجود
     پایپلاین (``audit_pipeline.start_audit_summary_job``، که خودش یک ترد
     جدا اجرا می‌کند) را روی آن راه‌اندازی می‌کند.
  ۳) دیکشنری خام job را به مدل‌های Pydantic قابل نمایش در API/HTML تبدیل
     می‌کند، شامل تبدیل سبک markdown->HTML برای پیش‌نمایش خلاصه در مرورگر.
"""
from __future__ import annotations

import html
import logging
import re
from pathlib import Path
from typing import Any, Callable, Optional

# نکته: ``await request.form()`` در لایه‌ی روتر همیشه نمونه‌هایی از
# ``starlette.datastructures.UploadFile`` برمی‌گرداند (نه زیرکلاس آن
# ``fastapi.UploadFile``)، به همین دلیل اینجا هم از همان نوع برای کامل بودن
# type hint استفاده می‌شود.
from starlette.datastructures import UploadFile

import audit_pipeline
from api.jobs.job_manager import JobManager, job_manager
from api.schemas.summary import JobResultsResponse, JobStatusResponse
from api.utils.uploads import InMemoryUploadAdapter
from api.workshops.registry import ResultArtifact
from api.services import ai_settings as ai_settings_service

logger = logging.getLogger(__name__)


class AuditSummaryValidationError(Exception):
    """خطای اعتبارسنجی سطح-درخواست (فایل الزامی گم است، پسوند/حجم نامعتبر است و...).

    پیام این خطا از قبل به فارسی و قابل‌نمایش مستقیم به کاربر است.
    """


SLUG = "audit-summary"


# ---------------------------------------------------------------------------
# تنظیمات هوش مصنوعی این کارگاه
# ---------------------------------------------------------------------------
def resolve_llm_settings(session: Any, project_id: int) -> Any:
    """تنظیمات مؤثر این کارگاه در این پروژه (ذخیره‌شده ← پیش‌فرض سامانه)."""
    resolved = ai_settings_service.resolve_ai_settings(session, project_id, SLUG)
    return ai_settings_service.to_audit_summary_llm_config(resolved)


_TEST_PROMPT = 'تنها همین متن را برگردان و بس: {"ok": true}'


def test_connection(settings: Any) -> str:
    """یک درخواست کمینه به مدل می‌فرستد و در صورت موفقیت پیام فارسی برمی‌گرداند."""
    import sys
    from pathlib import Path as _Path

    _summarizer_dir = str(_Path(__file__).resolve().parent.parent.parent / "audit_summarizer")
    if _summarizer_dir not in sys.path:
        sys.path.insert(0, _summarizer_dir)

    from llm_client import LLMConfig, call_llm, LLMClientError  # type: ignore[import]

    llm_config = ai_settings_service.to_audit_summary_llm_config(settings)
    probe = LLMConfig(
        api_key=llm_config.api_key,
        base_url=llm_config.base_url,
        model=llm_config.model,
        temperature=0.0,
        max_tokens=64,
        timeout_s=45,
        stream=False,
    )
    try:
        result = call_llm(_TEST_PROMPT, probe)
    except LLMClientError as exc:
        raise LLMClientError(str(exc)) from exc

    if not result or not result.strip():
        raise LLMClientError("مدل پاسخ خالی برگرداند.")

    return f"اتصال موفق بود؛ مدل «{probe.model}» پاسخ معتبر داد."


def _validate_upload(upload: UploadFile, max_bytes: int, content: bytes) -> None:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in audit_pipeline.ALLOWED_SUFFIXES:
        allowed = ", ".join(sorted(s.lstrip(".") for s in audit_pipeline.ALLOWED_SUFFIXES))
        raise AuditSummaryValidationError(
            f"فرمت فایل «{upload.filename}» پذیرفته نیست. فرمت‌های مجاز: {allowed}."
        )
    if len(content) > max_bytes:
        max_mb = max_bytes / (1024 * 1024)
        raise AuditSummaryValidationError(
            f"حجم فایل «{upload.filename}» بیش از حد مجاز ({max_mb:.0f} مگابایت) است."
        )
    if len(content) == 0:
        raise AuditSummaryValidationError(f"فایل «{upload.filename}» خالی است.")


async def start_job(
    upload: Optional[UploadFile],
    organization: Optional[str],
    meeting_context: Optional[str],
    max_upload_bytes: int,
    *,
    project_id: int,
    run_id: int,
    on_finish: Optional[Callable[[dict[str, Any]], None]] = None,
    llm_settings: Optional[Any] = None,
    manager: JobManager = job_manager,
) -> str:
    """اعتبارسنجی و ذخیره‌ی فایل آپلودی، سپس شروع پردازش پس‌زمینه‌ی خلاصه‌سازی.

    دقیقاً مطابق الگوی ``checklist_service.start_job``: فقط بخش سریع
    (اعتبارسنجی + ذخیره‌ی فایل روی دیسک) به‌صورت همزمان (await) اجرا می‌شود؛
    پردازش سنگین (استخراج متن + تماس با مدل زبانی + ساخت docx) توسط
    ``audit_pipeline.start_audit_summary_job`` در یک ترد پس‌زمینه‌ی جدا انجام
    می‌شود. ``project_id``/``run_id`` برچسب‌های job و ``on_finish`` ثبت‌کننده‌ی
    تاریخچه است.
    """
    if upload is None or not upload.filename:
        raise AuditSummaryValidationError("لطفاً یک فایل گزارش حسابرسی بارگذاری کنید.")

    workdir = audit_pipeline.make_audit_temp_workdir()
    try:
        content = await upload.read()
        _validate_upload(upload, max_upload_bytes, content)
        adapter = InMemoryUploadAdapter(upload.filename, content)
        input_path = audit_pipeline.save_audit_upload(adapter, workdir)
    except AuditSummaryValidationError:
        audit_pipeline.cleanup_audit_workdir(workdir)
        raise
    except Exception as exc:  # noqa: BLE001
        audit_pipeline.cleanup_audit_workdir(workdir)
        logger.exception("unexpected error while saving audit-summary upload")
        raise AuditSummaryValidationError(
            "خطای غیرمنتظره‌ای هنگام آماده‌سازی فایل بارگذاری‌شده رخ داد."
        ) from exc

    job_id, job = manager.create(
        audit_pipeline.new_audit_job,
        workdir=workdir,
        kind="audit-summary",
        project_id=project_id,
        run_id=run_id,
        on_finish=on_finish,
    )
    # برچسب‌های خصوصی متادیتای اجرا (خودِ audit_pipeline این‌ها را نمی‌خواند) --
    # فقط برای ثبت در ردیف تاریخچه.
    job["_source_filename"] = upload.filename
    job["_organization"] = (organization or "").strip() or None
    job["_meeting_context"] = (meeting_context or "").strip() or None
    audit_pipeline.start_audit_summary_job(
        job,
        input_path,
        workdir,
        organization=(organization or "").strip() or None,
        meeting_context=(meeting_context or "").strip() or None,
        llm_config=llm_settings,
    )
    manager.watch_lifecycle(job_id, job)
    logger.info(
        "audit-summary job %s started (project=%s, run=%s, source=%r)",
        job_id,
        project_id,
        run_id,
        upload.filename,
    )
    return job_id


# ---------------------------------------------------------------------------
# تخمین درصد پیشرفت از روی متن مرحله‌ی جاری (job["stage"])، مشابه
# checklist_service.estimate_progress اما بر اساس نشانگرهای [n/4] که
# audit_pipeline.run_audit_summary تولید می‌کند.
# ---------------------------------------------------------------------------
_STAGE_PROGRESS_RE = re.compile(r"^\[(\d)/4\]")


def estimate_progress(job: dict[str, Any]) -> int:
    """درصد پیشرفت تقریبی (عمومی -- پنل کارهای در جریان پروژه هم از آن استفاده می‌کند)."""
    status = job.get("status")
    if status == "done":
        return 100
    if status == "pending":
        return 0

    stage = job.get("stage") or ""
    match = _STAGE_PROGRESS_RE.match(stage)
    if match:
        step = int(match.group(1))
        return {1: 10, 2: 35, 3: 80, 4: 95}.get(step, 50)

    if "در حال شروع" in stage:
        return 3

    return min(90, 5 + 3 * len(job.get("logs", [])))


def build_status_response(job_id: str, job: dict[str, Any]) -> JobStatusResponse:
    status = job.get("status", "pending")
    result = job.get("result") or {}
    elapsed_seconds = result.get("elapsed_seconds") if status == "done" else None

    return JobStatusResponse(
        job_id=job_id,
        run_id=job.get("_run_id"),
        status=status,
        stage=job.get("stage", ""),
        progress=estimate_progress(job),
        logs=job.get("logs", [])[-20:],
        error=job.get("error"),
        summary_ready=bool(result.get("docx_bytes")),
        elapsed_seconds=elapsed_seconds,
    )


# ---------------------------------------------------------------------------
# تبدیل سبک markdown->HTML برای پیش‌نمایش خلاصه در مرورگر (فقط لایه‌ی نمایش،
# هیچ اثری روی audit_summarizer/doc_writer.py که سند Word را می‌سازد ندارد).
# قوانین تشخیص عنوان/لیست/بولد عمداً با الگوهای doc_writer._HEADING_RE /
# _LIST_ITEM_RE / _INLINE_BOLD_RE هماهنگ نگه داشته شده‌اند.
# ---------------------------------------------------------------------------
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*•]|\d+[.\)])\s+(.*)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

_HEADING_TAG_BY_LEVEL = {1: "h2", 2: "h3", 3: "h4"}
_HEADING_SIZE_BY_LEVEL = {1: "text-lg", 2: "text-base", 3: "text-sm"}


def _inline_html(text: str) -> str:
    parts: list[str] = []
    pos = 0
    for match in _BOLD_RE.finditer(text):
        if match.start() > pos:
            parts.append(html.escape(text[pos : match.start()]))
        parts.append(f"<strong>{html.escape(match.group(1))}</strong>")
        pos = match.end()
    parts.append(html.escape(text[pos:]))
    return "".join(parts)


def markdown_to_preview_html(summary_markdown: str) -> str:
    """تبدیل ساده‌ی متن markdown-\u200cمانند خروجی مدل زبانی به HTML امن برای
    پیش‌نمایش در صفحه‌ی نتایج (بدون هیچ وابستگی جدید)."""
    parts: list[str] = []
    list_buffer: list[str] = []

    def flush_list() -> None:
        if list_buffer:
            items = "".join(f"<li>{item}</li>" for item in list_buffer)
            parts.append(f'<ul class="list-disc pe-5 space-y-1 mb-2">{items}</ul>')
            list_buffer.clear()

    for raw_line in (summary_markdown or "").splitlines():
        line = raw_line.strip()
        if not line:
            flush_list()
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            flush_list()
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()
            tag = _HEADING_TAG_BY_LEVEL.get(level, "h4")
            size_class = _HEADING_SIZE_BY_LEVEL.get(level, "text-sm")
            parts.append(
                f'<{tag} class="{size_class} font-bold text-slate-900 mt-4 mb-2">'
                f"{_inline_html(text)}</{tag}>"
            )
            continue

        list_match = _LIST_ITEM_RE.match(line)
        if list_match:
            list_buffer.append(_inline_html(list_match.group(1).strip()))
            continue

        flush_list()
        parts.append(f'<p class="text-sm leading-7 text-slate-700 mb-2">{_inline_html(line)}</p>')

    flush_list()
    return "".join(parts)


def build_results_response(job_id: str, job: dict[str, Any]) -> JobResultsResponse:
    result = job.get("result") or {}
    summary_markdown = result.get("summary_markdown", "")
    return JobResultsResponse(
        job_id=job_id,
        summary_markdown=summary_markdown,
        summary_html=markdown_to_preview_html(summary_markdown),
        warnings=result.get("warnings", []),
        source_filename=result.get("source_filename", ""),
        elapsed_seconds=result.get("elapsed_seconds"),
    )


def get_report_bytes(job: dict[str, Any]) -> tuple[bytes, str]:
    result = job.get("result") or {}
    docx_bytes = result.get("docx_bytes")
    if not docx_bytes:
        raise AuditSummaryValidationError("خلاصه‌ی Word برای این پردازش در دسترس نیست.")
    filename = result.get("docx_filename") or "خلاصه_گزارش_حسابرسی.docx"
    return docx_bytes, filename


# ---------------------------------------------------------------------------
# ثبت تاریخچه: تبدیل job تمام‌شده به artifact ماندگار
# ---------------------------------------------------------------------------
def has_download(job: dict[str, Any]) -> bool:
    """آیا همین حالا فایل نتیجه‌ی این job قابل دانلود است؟"""
    return bool((job.get("result") or {}).get("docx_bytes"))


def summary_chips_fa(summary: dict[str, Any]) -> list[str]:
    """برچسب‌های کوتاه خلاصه‌ی نتیجه‌ی خلاصه‌سازی برای فهرست تاریخچه."""
    from api.utils.formatting import fa_number

    chips: list[str] = []
    if summary.get("source_filename"):
        chips.append(f"برگرفته از: {summary['source_filename']}")
    if summary.get("organization"):
        chips.append(str(summary["organization"]))
    if summary.get("summary_chars"):
        chips.append(f"{fa_number(summary['summary_chars'])} نویسه خلاصه")
    warnings_count = len(summary.get("warnings") or [])
    if warnings_count:
        chips.append(f"{fa_number(warnings_count)} هشدار پردازش")
    return chips


def collect_result(job: dict[str, Any]) -> ResultArtifact:
    """خروجی نهایی خلاصه‌سازی را برای ذخیره در تاریخچه آماده می‌کند.

    در این کارگاه خودِ «خلاصه» نتیجه‌ی اصلی است (نه فقط فایل Word آن)، بنابراین
    متن خلاصه هم در ``result_summary`` ذخیره می‌شود تا کاربر بعداً بدون اجرای دوباره
    بتواند آن را بخواند. هیچ محتوایی از فایل ورودی (گزارش حسابرسی) -- نه متن
    استخراج‌شده و نه مسیر فایل -- ذخیره نمی‌شود؛ تنها نام فایل مبدأ به‌عنوان
    متادیتای اجرا باقی می‌ماند.
    """
    result = job.get("result") or {}
    summary_markdown = result.get("summary_markdown", "")
    filename = result.get("docx_filename") or "خلاصه_گزارش_حسابرسی.docx"

    summary = {
        "source_filename": job.get("_source_filename") or result.get("source_filename") or "",
        "organization": job.get("_organization"),
        "meeting_context": job.get("_meeting_context"),
        "warnings": list(result.get("warnings") or [])[:10],
        "elapsed_seconds": result.get("elapsed_seconds"),
        "summary_markdown": summary_markdown,
        "summary_chars": len(summary_markdown or ""),
    }

    content = result.get("docx_bytes")
    if content:
        summary["result_filename"] = filename

    return ResultArtifact(summary=summary, filename=filename, content=content)
