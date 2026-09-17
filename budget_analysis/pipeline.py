"""اجرای خط سه‌مرحله‌ای تحلیل بودجه به‌صورت یک job پس‌زمینه.

این ماژول همان قرارداد سایر کارگاه‌های پروژه را رعایت می‌کند: یک ``job`` دیکشنری
با کلیدهای ``status``/``stage``/``logs``/``result``/``error`` که یک ترد پس‌زمینه
آن را درجا به‌روزرسانی می‌کند (``JobManager`` از همان قرارداد پشتیبانی می‌کند).
سه مرحله‌ی پردازش با پیام «مرحله n از ۳» گزارش می‌شوند تا درصد پیشرفت قابل‌محاسبه
باشد.

فایل‌های میانی (JSON مرحله‌ی ۱ و خروجی خام مرحله‌ی ۲) در همان پوشه‌ی موقت کار
نوشته و در پایان -- همراه با فایل‌های ورودی -- حذف می‌شوند؛ فقط گزارش Word به
لایه‌ی داشبورد برمی‌گردد.
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any, Optional

from budget_analysis.analysis import BudgetAnalysisError, analyze_budget, apply_known_facts
from budget_analysis.config import BudgetConfig
from budget_analysis.extraction import extract_bundle
from budget_analysis.grid import ExtractionGridError
from budget_analysis.llm import BudgetAnalysisLLM, BudgetLLMSettings
from budget_analysis.report import ReportContext, render_report_docx
from budget_analysis.schemas import BudgetAnalysisReport, ExtractionBundle

logger = logging.getLogger(__name__)

__all__ = [
    "BudgetPipelineError",
    "REPORT_FILENAME",
    "WORKDIR_PREFIX",
    "cleanup_workdir",
    "make_temp_workdir",
    "new_budget_job",
    "run_budget_analysis",
    "set_stage",
    "start_budget_job",
]

WORKDIR_PREFIX = "psa_budget_"
REPORT_FILENAME = "گزارش_تحلیل_بودجه.docx"

# نام فایل‌های میانی مرحله‌ی ۱ و ۲ -- فقط برای عیب‌یابی و در پایان حذف می‌شوند.
INTERMEDIATE_EXTRACTION = "01_extraction.json"
INTERMEDIATE_ANALYSIS = "02_analysis.json"

STAGES_FA = (
    "مرحله ۱ از ۳: استخراج ساختارمند فرم‌ها، ردیف‌ها و مقادیر از اسناد بودجه...",
    "مرحله ۲ از ۳: تحلیل معیارها، ساخت ماتریس خطادهی و یافته‌های مدیریتی با هوش مصنوعی...",
    "مرحله ۳ از ۳: تولید گزارش مدیریتی Word با قالب‌بندی فارسی...",
)


class BudgetPipelineError(Exception):
    """خطای سطح-پایپ‌لاین با پیام فارسی قابل‌نمایش به کاربر."""


# ---------------------------------------------------------------------------
# پوشه‌ی کار موقت
# ---------------------------------------------------------------------------
def make_temp_workdir() -> Path:
    return Path(tempfile.mkdtemp(prefix=WORKDIR_PREFIX))


def cleanup_workdir(path: Optional[Path]) -> None:
    """پوشه‌ی کار (شامل اسناد ورودی و JSONهای میانی) را حذف می‌کند."""
    if path is not None:
        shutil.rmtree(path, ignore_errors=True)


def set_stage(job: Optional[dict[str, Any]], message: str) -> None:
    """مرحله‌ی جاری را برای نمایش زنده در داشبورد ثبت می‌کند."""
    if job is None:
        return
    job["stage"] = message
    job.setdefault("logs", []).append(message)


def new_budget_job() -> dict[str, Any]:
    """دیکشنری وضعیت اولیه‌ی یک اجرای تحلیل بودجه."""
    return {
        "status": "idle",
        "result": None,
        "error": None,
        "thread": None,
        "started_at": None,
        "stage": "",
        "logs": [],
    }


# ---------------------------------------------------------------------------
# خط پردازش
# ---------------------------------------------------------------------------
def run_budget_analysis(
    file_paths: dict[str, Path],
    *,
    workdir: Path,
    config: Optional[BudgetConfig] = None,
    organization: Optional[str] = None,
    meeting_context: Optional[str] = None,
    report_date: Optional[str] = None,
    font: Optional[str] = None,
    job: Optional[dict[str, Any]] = None,
    llm: Optional[BudgetAnalysisLLM] = None,
    extraction: Any = None,
    analyze: Any = None,
    filenames: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """اجرای کامل سه مرحله و برگرداندن artifact نهایی.

    ``extraction`` و ``analyze`` قابل‌تزریق هستند تا تست‌ها (و فازهای بعدی) بتوانند
    مرحله‌ی مدل زبانی را بدون تماس واقعی جایگزین کنند -- همان الگوی سایر کارگاه‌ها.
    مقدار پیش‌فرض در زمان *فراخوانی* از ماژول خوانده می‌شود (نه در زمان تعریف تابع)
    تا monkeypatch کردن ``budget_analysis.pipeline.analyze_budget`` هم اثر کند.

    Raises:
        BudgetPipelineError: خطای مرحله‌ی استخراج/تحلیل با پیام فارسی.
    """
    started = time.time()
    config = config or BudgetConfig()
    extract_fn = extraction or extract_bundle
    analyze_fn = analyze or analyze_budget

    # --- مرحله ۱ ---
    set_stage(job, STAGES_FA[0])
    try:
        bundle: ExtractionBundle = extract_fn(file_paths, config=config, filenames=filenames)
    except ExtractionGridError as exc:
        raise BudgetPipelineError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("budget extraction stage failed")
        raise BudgetPipelineError(f"استخراج داده از اسناد بودجه ناموفق بود: {exc}") from exc

    if not bundle.documents:
        raise BudgetPipelineError("هیچ سند قابل‌تحلیلی دریافت نشد؛ هر دو سند بودجه الزامی است.")

    _write_intermediate(workdir, INTERMEDIATE_EXTRACTION, bundle.model_dump())
    for warning in bundle.warnings:
        set_stage(job, f"هشدار: {warning}")
    set_stage(
        job,
        "مرحله ۱ از ۳: %d سند و %d قلم عددی استخراج شد."
        % (len(bundle.documents), bundle.total_cells),
    )

    # --- مرحله ۲ ---
    try:
        report: BudgetAnalysisReport = analyze_fn(
            bundle,
            config,
            organization=organization,
            meeting_context=meeting_context,
            llm=llm,
            report_stage=lambda message: set_stage(job, message),
        )
    except BudgetAnalysisError as exc:
        raise BudgetPipelineError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("budget analysis stage failed")
        raise BudgetPipelineError(f"تحلیل بودجه ناموفق بود: {exc}") from exc

    # تثبیت مستقل واقعیت‌های قطعی (سال‌ها، نام اسناد) -- حتی اگر مرحله‌ی تحلیل با
    # پیاده‌سازی تزریق‌شده‌ای اجرا شده باشد که خودش این کار را نکند.
    report = apply_known_facts(report, bundle, organization=organization)

    _write_intermediate(workdir, INTERMEDIATE_ANALYSIS, report.model_dump())

    # --- مرحله ۳ ---
    set_stage(job, STAGES_FA[2])
    context = ReportContext(
        organization=(organization or "").strip() or "",
        base_year=bundle.base_year or "",
        current_year=bundle.current_year or "",
        unit=_shared_unit(bundle),
        source_documents=[document.filename for document in bundle.documents],
        report_date=report_date or date.today().isoformat(),
        font=font or ReportContext.font,
    )
    output_path = workdir / REPORT_FILENAME
    try:
        render_report_docx(report, context, output_path)
    except Exception as exc:  # noqa: BLE001
        logger.exception("budget report rendering failed")
        raise BudgetPipelineError(f"تولید فایل Word گزارش ناموفق بود: {exc}") from exc

    elapsed = time.time() - started
    set_stage(job, "پردازش با موفقیت به اتمام رسید.")

    warnings = list(bundle.warnings)
    for document in bundle.documents:
        warnings.extend(document.warnings)

    return {
        "report": report,
        "docx_bytes": output_path.read_bytes(),
        "docx_filename": REPORT_FILENAME,
        "warnings": warnings[:30],
        "elapsed_seconds": elapsed,
        "extraction_methods": [document.extraction_method for document in bundle.documents],
        "extraction_summary": [
            {
                "slot": document.slot,
                "role_fa": document.role_fa,
                "filename": document.filename,
                "detected_year": document.detected_year,
                "unit": document.unit,
                "method": document.extraction_method,
                "forms_found": [form.form_name for form in document.forms],
                "forms_missing": list(document.missing_form_keys),
                "cell_count": len(document.cells),
            }
            for document in bundle.documents
        ],
    }


def _shared_unit(bundle: ExtractionBundle) -> Optional[str]:
    units = {document.unit for document in bundle.documents if document.unit}
    return next(iter(units)) if len(units) == 1 else None


def _write_intermediate(workdir: Path, name: str, payload: Any) -> None:
    """نوشتن JSON میانی برای عیب‌یابی -- در پایان همراه با پوشه‌ی کار حذف می‌شود."""
    try:
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / name).write_text(
            json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8"
        )
    except OSError:
        logger.warning("writing intermediate file %s failed", name, exc_info=True)


# ---------------------------------------------------------------------------
# اجرای پس‌زمینه
# ---------------------------------------------------------------------------
def start_budget_job(
    job: dict[str, Any],
    file_paths: dict[str, Path],
    workdir: Path,
    *,
    config: Optional[BudgetConfig] = None,
    organization: Optional[str] = None,
    meeting_context: Optional[str] = None,
    report_date: Optional[str] = None,
    filenames: Optional[dict[str, str]] = None,
    llm: Optional[BudgetAnalysisLLM] = None,
    settings: Optional[BudgetLLMSettings] = None,
    analyze: Any = None,
) -> None:
    """اجرای ``run_budget_analysis`` در یک ترد پس‌زمینه (مثل سایر کارگاه‌ها)."""

    def _worker() -> None:
        job["status"] = "running"
        try:
            client = llm or (BudgetAnalysisLLM(settings) if settings is not None else None)
            kwargs: dict[str, Any] = {
                "workdir": workdir,
                "config": config,
                "organization": organization,
                "meeting_context": meeting_context,
                "report_date": report_date,
                "filenames": filenames,
                "job": job,
                "llm": client,
            }
            if analyze is not None:
                kwargs["analyze"] = analyze
            job["result"] = run_budget_analysis(file_paths, **kwargs)
            job["status"] = "done"
        except BudgetPipelineError as exc:
            job["error"] = str(exc)
            job["status"] = "error"
        except Exception as exc:  # noqa: BLE001
            logger.exception("unexpected error in budget analysis job")
            job["error"] = f"خطای نامشخص در پردازش: {exc}"
            job.setdefault("logs", []).append(f"خطای نامشخص: {exc}")
            job["status"] = "error"
        finally:
            cleanup_workdir(workdir)

    job["status"] = "running"
    job["started_at"] = time.time()
    job["stage"] = "در حال شروع..."
    job["logs"] = []
    thread = threading.Thread(target=_worker, name="budget-analysis", daemon=True)
    job["thread"] = thread
    thread.start()


def stage_progress_hint(stage: str) -> Optional[int]:
    """درصد تقریبی پیشرفت از روی متن مرحله (کمک به لایه‌ی نمایش)."""
    mapping = (
        ("مرحله ۱ از ۳", 12),
        ("مرحله ۲ از ۳", 55),
        ("مرحله ۳ از ۳", 88),
        ("پردازش با موفقیت به اتمام رسید", 100),
    )
    for marker, progress in mapping:
        if marker in stage:
            return progress
    return None
