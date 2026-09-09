"""
audit_pipeline.py
----------------
پوسته‌ی سبک روی بسته‌ی audit_summarizer برای استفاده در داشبورد (main/app.py).

برخلاف main/pipeline.py (که چک‌لیست حسابرسی مالی را روی فایل‌های اکسل اجرا
می‌کند)، این ماژول یک گزارش حسابرسی متنی (PDF/DOC/DOCX) را می‌گیرد و با کمک
مدل زبانی، خلاصه‌ی قابل‌فهم و آماده‌ی جلسه تولید می‌کند (معادل audit_summarizer/main.py
اما بدون CLI و به‌صورت مستقیماً قابل‌فراخوانی، برای اجرا در ترد پس‌زمینه).

این پردازش ممکن است طول بکشد (تماس شبکه‌ای با مدل زبانی)؛ به همین دلیل توابع
این ماژول برای اجرا در یک ترد جداگانه طراحی شده‌اند تا بقیه‌ی داشبورد
(چک‌لیست حسابرسی مالی) مسدود نشود. وضعیت اجرا و لاگ‌های پردازش در یک دیکشنری
معمولی (job dict) نگه داشته می‌شود که مستقیماً از ترد پس‌زمینه به‌روزرسانی
می‌شود (بدون فراخوانی st.session_state از آن ترد) تا با مدل اجرای Streamlit
سازگار بماند.

نکته درباره‌ی رمزگذاری متن:
    نسخه‌ی پیش‌فرض LLMConfig جریانی (stream=True) است. کتابخانه‌ی requests در
    حالت streaming، انکودینگ پاسخ را از هدر Content-Type تشخیص می‌دهد و اگر
    سرور charset را مشخص نکند، ممکن است متن چندبایتی UTF-8 (فارسی) را اشتباه
    دیکد کند و خروجی را به‌صورت mojibake (مثل "Ø®ÙØ§ØµØªÛ") تبدیل کند. به همین
    دلیل اینجا صراحتاً stream=False استفاده می‌شود (دقیقاً مانند رفتار پیش‌فرض
    CLI در audit_summarizer/main.py که --stream پاس نشود) که در آن پاسخ کامل
    یک‌جا و با response.json() (که خودش صحیح UTF-8 را می‌فهمد) خوانده می‌شود.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
AUDIT_SUMMARIZER_DIR = ROOT_DIR / "audit_summarizer"
if str(AUDIT_SUMMARIZER_DIR) not in sys.path:
    sys.path.insert(0, str(AUDIT_SUMMARIZER_DIR))

from doc_writer import render_summary_to_docx  # noqa: E402
from file_ingest import ExtractionError, extract_text  # noqa: E402
from llm_client import (  # noqa: E402
    LLMClientError,
    LLMConfig,
    build_summary_prompt,
    call_llm,
)

ALLOWED_SUFFIXES = {".pdf", ".doc", ".docx"}

# طول حداکثری هر خط لاگ. خطوطی طولانی‌تر (معمولاً JSON خام پاسخ مدل) کوتاه
# می‌شوند تا نمایش لاگ در داشبورد شلوغ نشود.
MAX_LOG_LINE_CHARS = 700


class AuditSummaryError(Exception):
    """خطای سطح بالا برای مراحل خلاصه‌سازی گزارش حسابرسی که باید به کاربر نمایش داده شود."""


def make_audit_temp_workdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="psa_audit_summary_"))


def cleanup_audit_workdir(path: Optional[Path]) -> None:
    if path is not None:
        shutil.rmtree(path, ignore_errors=True)


def save_audit_upload(uploaded_file, dest_dir: Path) -> Path:
    """ذخیره فایل بارگذاری‌شده در Streamlit روی دیسک، پیش از شروع ترد پس‌زمینه."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / uploaded_file.name
    with open(dest_path, "wb") as fh:
        fh.write(uploaded_file.getbuffer())
    return dest_path


def _make_logger(job: Optional[dict[str, Any]]):
    """
    ساخت یک تابع لاگ‌گیری ساده که خطوط را به job["logs"] اضافه می‌کند (اگر job
    داده شده باشد) - دقیقاً معادل همان پیام‌هایی که audit_summarizer/main.py
    با print() چاپ می‌کرد، اما اینجا برای نمایش در داشبورد ذخیره می‌شوند.
    خطوط بیش‌ازحد طولانی (مثل JSON خام پاسخ مدل) کوتاه می‌شوند.
    """

    def _log(text: str) -> None:
        if job is None:
            return
        if len(text) > MAX_LOG_LINE_CHARS:
            text = (
                text[:MAX_LOG_LINE_CHARS]
                + f"\n… [کوتاه‌شده برای خوانایی - طول کامل: {len(text)} کاراکتر]"
            )
        job.setdefault("logs", []).append(text)

    return _log


def run_audit_summary(
    input_path: Path,
    workdir: Path,
    *,
    job: Optional[dict[str, Any]] = None,
    organization: str | None = None,
    meeting_context: str | None = None,
    language: str = "fa",
) -> dict[str, Any]:
    """
    اجرای مستقیم خط پردازش audit_summarizer (معادل تابع run در audit_summarizer/main.py):
    استخراج متن -> ساخت prompt -> فراخوانی مدل زبانی -> رندر سند Word خلاصه.

    اگر `job` داده شود، همان پیام‌های پیشرفت که نسخه‌ی CLI با print() چاپ
    می‌کرد در job["logs"] ذخیره می‌شوند تا در داشبورد نمایش داده شوند.

    خروجی، دیکشنری قابل‌نمایش در داشبورد است (شامل بایت‌های فایل docx نهایی).
    """
    log = _make_logger(job)
    start = time.time()

    log(f"[1/4] در حال استخراج متن از: {input_path.name}")
    try:
        extracted = extract_text(input_path)
    except FileNotFoundError as exc:
        log(f"خطا: فایل یافت نشد: {exc}")
        raise AuditSummaryError(f"فایل یافت نشد: {exc}") from exc
    except ExtractionError as exc:
        log(f"خطا در استخراج متن: {exc}")
        raise AuditSummaryError(f"خطا در استخراج متن از فایل: {exc}") from exc

    warnings = list(getattr(extracted, "warnings", []) or [])
    for w in warnings:
        log(f"  هشدار: {w}")

    prompt_text = extracted.as_prompt_text(max_chars=100_000)
    log(f"  {len(prompt_text)} کاراکتر متن استخراج شد.")

    log("[2/4] در حال ساخت prompt و فراخوانی مدل...")
    prompt = build_summary_prompt(
        prompt_text, language=language, organization_name=organization
    )
    # stream=False: دقیقاً مانند رفتار پیش‌فرض CLI، برای جلوگیری از باگ
    # mojibake شناخته‌شده‌ی requests در حالت streaming (نگاه کنید به docstring بالا).
    config = LLMConfig(stream=False)
    try:
        summary_markdown = call_llm(prompt, config, logger=log)
    except LLMClientError as exc:
        log(f"خطا در تماس با مدل: {exc}")
        raise AuditSummaryError(f"خطا در تماس با مدل هوش مصنوعی: {exc}") from exc

    log("[3/4] در حال ساخت سند خروجی...")
    docx_path = workdir / f"{input_path.stem}_خلاصه.docx"
    render_summary_to_docx(
        summary_markdown,
        docx_path,
        organization=organization,
        meeting_context=meeting_context,
        source_filename=input_path.name,
    )
    docx_bytes = docx_path.read_bytes()
    log("[4/4] سند Word خلاصه با موفقیت ساخته شد.")
    log(f"گزارش نهایی آماده شد: {docx_path.name}")

    return {
        "summary_markdown": summary_markdown,
        "docx_bytes": docx_bytes,
        "docx_filename": docx_path.name,
        "warnings": warnings,
        "elapsed_seconds": time.time() - start,
        "source_filename": input_path.name,
    }


def new_audit_job() -> dict[str, Any]:
    """یک دیکشنری وضعیت اولیه برای پیگیری اجرای پس‌زمینه‌ی خلاصه‌سازی."""
    return {
        "status": "idle",  # idle | running | done | error
        "result": None,
        "error": None,
        "thread": None,
        "started_at": None,
        "source_filename": None,
        "logs": [],
    }


def start_audit_summary_job(
    job: dict[str, Any],
    input_path: Path,
    workdir: Path,
    **kwargs: Any,
) -> None:
    """
    اجرای run_audit_summary در یک ترد پس‌زمینه‌ی جدا.

    نکته‌ی مهم: ترد پس‌زمینه هرگز مستقیماً st.session_state[...] را ست نمی‌کند؛
    فقط فیلدهای درونی همین دیکشنری job (که از قبل در session_state ذخیره شده)
    را تغییر می‌دهد (append به لیست / انتساب مقدار ساده). این کار باعث می‌شود
    بدون نیاز به ScriptRunContext با مدل اجرای ترد پس‌زمینه‌ی Streamlit سازگار
    بماند.
    """

    def _worker() -> None:
        job["status"] = "running"
        try:
            result = run_audit_summary(input_path, workdir, job=job, **kwargs)
            job["result"] = result
            job["status"] = "done"
        except AuditSummaryError as exc:
            job["error"] = str(exc)
            job["status"] = "error"
        except Exception as exc:  # noqa: BLE001
            job["error"] = f"خطای غیرمنتظره در پردازش: {exc}"
            job.setdefault("logs", []).append(f"خطای غیرمنتظره: {exc}")
            job["status"] = "error"
        finally:
            cleanup_audit_workdir(workdir)

    job["status"] = "running"
    job["started_at"] = time.time()
    job["source_filename"] = input_path.name
    job["logs"] = []
    thread = threading.Thread(target=_worker, daemon=True)
    job["thread"] = thread
    thread.start()
