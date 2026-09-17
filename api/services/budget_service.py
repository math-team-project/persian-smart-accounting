"""لایه‌ی سرویس کارگاه «تحلیل بودجه».

این ماژول هیچ منطق استخراج/تحلیلی ندارد -- یک لایه‌ی نازک روی پکیج
``budget_analysis`` است، دقیقاً به همان الگوی ``checklist_service`` و
``summary_service``:

  ۱) فایل‌های آپلودی FastAPI را اعتبارسنجی و روی دیسک ذخیره می‌کند،
  ۲) یک job در ``job_manager`` عمومی می‌سازد و خط سه‌مرحله‌ای تحلیل را در یک ترد
     پس‌زمینه راه می‌اندازد،
  ۳) دیکشنری خام job را به مدل‌های Pydantic قابل‌نمایش در API/HTML تبدیل می‌کند،
  ۴) نتیجه‌ی نهایی را برای ثبت در تاریخچه‌ی پروژه آماده می‌کند (فقط گزارش Word و
     متادیتای شمارشی نتیجه؛ نه فایل ورودی و نه JSONهای میانی).

تنظیمات مدل زبانی عمداً از طریق ``BudgetLLMSettings`` پاس داده می‌شود تا فاز
تنظیمات بتواند بدون بازنویسی، کلید/آدرس/مدل هر پروژه را تزریق کند.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from api.jobs.job_manager import JobManager, job_manager
from api.schemas.budget import (
    AxisRow,
    ClarificationRow,
    DecisionRow,
    FindingRow,
    JobResultsResponse,
    JobStatusResponse,
    MatrixRow,
    RiskRow,
    TopFindingRow,
)
from api.services import ai_settings as ai_settings_service
from api.utils.formatting import fa_date, utcnow
from api.workshops.registry import ResultArtifact
# اسلاگ کارگاه از خود پکیج ``budget_analysis`` خوانده می‌شود (منبع واحد نام آن) تا
# این رشته در چند فایل تکرار نشود.
from budget_analysis import SLUG
from budget_analysis import pipeline as budget_pipeline
from budget_analysis.config import BudgetConfig
from budget_analysis.llm import BudgetAnalysisLLM, BudgetLLMError, BudgetLLMSettings
from budget_analysis.schemas import BudgetAnalysisReport

logger = logging.getLogger(__name__)

__all__ = [
    "BudgetValidationError",
    "FILE_SLOTS",
    "build_results_response",
    "build_status_response",
    "collect_result",
    "estimate_progress",
    "get_file_slots",
    "has_download",
    "start_job",
    "summary_chips_fa",
    "test_connection",
]

DEFAULT_REPORT_FILENAME = budget_pipeline.REPORT_FILENAME

# اسلات‌های آپلود این کارگاه با همان قالب دیکشنری کامپوننت مشترک dropzone.html
# (label/help/required/icon/types). کلید ``extract_slot`` نشان می‌دهد هر فایل در
# مرحله‌ی استخراج کدام نقش (سال پایه/سال جاری) را دارد.
FILE_SLOTS: dict[str, dict[str, Any]] = {
    "budget_base_year": {
        "label": "اصلاحیه بودجه تفصیلی سال پایه",
        "help": "سند اصلاحیه بودجه تفصیلی سال گذشته (فرم‌های ۱ تا ۱۰)",
        "required": True,
        "icon": "file-spreadsheet",
        "types": ["xlsx", "xls", "pdf"],
        "extract_slot": "base_year",
    },
    "budget_current_year": {
        "label": "اصلاحیه بودجه تفصیلی سال جاری",
        "help": "سند اصلاحیه بودجه تفصیلی سال مورد بررسی (فرم‌های ۱ تا ۱۰)",
        "required": True,
        "icon": "file-spreadsheet",
        "types": ["xlsx", "xls", "pdf"],
        "extract_slot": "current_year",
    },
}

class BudgetValidationError(Exception):
    """خطای اعتبارسنجی سطح-درخواست؛ پیام آن فارسی و قابل‌نمایش به کاربر است."""


def get_file_slots() -> dict[str, dict[str, Any]]:
    """اسلات‌های آپلود برای رندر داینامیک در قالب."""
    return FILE_SLOTS


# ---------------------------------------------------------------------------
# تنظیمات هوش مصنوعی این کارگاه
# ---------------------------------------------------------------------------
def resolve_llm_settings(session: Session, project_id: int) -> BudgetLLMSettings:
    """تنظیمات مؤثر این کارگاه در این پروژه (ذخیره‌شده ← پیش‌فرض سامانه).

    تنها نقطه‌ی اتصال این کارگاه به جدول ``workshop_settings`` است: بقیه‌ی خط
    پردازش فقط ``BudgetLLMSettings`` آماده را می‌شناسد و از منبع آن بی‌خبر است.
    """
    resolved = ai_settings_service.resolve_ai_settings(session, project_id, SLUG)
    return ai_settings_service.to_budget_llm_settings(resolved)


# دستور کمینه‌ی «تست اتصال»: کوتاه، بی‌هزینه و با خروجی JSON قابل‌اعتبارسنجی، تا
# فقط «کار می‌کند یا نه» سنجیده شود -- نه کیفیت تحلیل.
TEST_SYSTEM_PROMPT = (
    "تو یک سرویس فنی هستی. تنها وظیفه‌ات پاسخ به قالب JSON خواسته‌شده است. "
    "هیچ توضیح اضافه‌ای نده."
)
TEST_USER_PROMPT = 'دقیقاً همین JSON را برگردان: {"ok": true}'


def test_connection(settings: Any) -> str:
    """یک درخواست کمینه به مدل می‌فرستد و در صورت موفقیت پیام فارسی برمی‌گرداند.

    پارامتر ورودی یک ``AISettings`` حل‌شده است (همان چیزی که رجیستری کارگاه‌ها
    به تابع تست پاس می‌دهد). سهمیه‌ی توکن و مهلت زمانی برای این تست کوچک نگه
    داشته می‌شوند تا دکمه‌ی «تست اتصال» سریع و کم‌هزینه باشد.

    Raises:
        budget_analysis.llm.BudgetLLMError: با پیام فارسی قابل‌نمایش به کاربر.
    """
    llm_settings = ai_settings_service.to_budget_llm_settings(settings)
    probe = replace(
        llm_settings,
        temperature=0.0,
        max_output_tokens=64,
        request_timeout=45.0,
        max_retries=1,
    )
    client = BudgetAnalysisLLM(probe)
    payload = client.complete_json(
        [
            {"role": "system", "content": TEST_SYSTEM_PROMPT},
            {"role": "user", "content": TEST_USER_PROMPT},
        ]
    )
    if not isinstance(payload, dict) or not payload.get("ok"):
        # پاسخ غیرمنتظره هم یعنی «اتصال برقرار است اما مدل با قرارداد ما سازگار
        # نیست» -- این هم باید به کاربر گفته شود، نه اینکه بی‌صدا موفق اعلام شود.
        raise BudgetLLMError(
            "اتصال برقرار شد اما پاسخ مدل با قالب موردانتظار سازگار نبود: "
            f"{str(payload)[:200]}"
        )
    return f"اتصال موفق بود؛ مدل «{probe.model}» پاسخ معتبر داد."


# ---------------------------------------------------------------------------
# شروع اجرا
# ---------------------------------------------------------------------------
def _validate_upload(slot_key: str, upload: UploadFile, max_bytes: int, content: bytes) -> None:
    slot = FILE_SLOTS[slot_key]
    suffix = Path(upload.filename or "").suffix.lower().lstrip(".")
    allowed = [item.lower() for item in slot.get("types", [])]
    if suffix not in allowed:
        raise BudgetValidationError(
            f"فرمت فایل «{upload.filename}» برای «{slot['label']}» پذیرفته نیست. "
            f"فرمت‌های مجاز: {', '.join(allowed)}."
        )
    if len(content) > max_bytes:
        max_mb = max_bytes / (1024 * 1024)
        raise BudgetValidationError(
            f"حجم فایل «{upload.filename}» بیش از حد مجاز ({max_mb:.0f} مگابایت) است."
        )
    if len(content) == 0:
        raise BudgetValidationError(f"فایل «{upload.filename}» خالی است.")


async def start_job(
    uploads: dict[str, Optional[UploadFile]],
    options: dict[str, Optional[str]],
    max_upload_bytes: int,
    *,
    project_id: int,
    run_id: int,
    on_finish: Optional[Callable[[dict[str, Any]], None]] = None,
    settings: Optional[BudgetLLMSettings] = None,
    manager: JobManager = job_manager,
) -> str:
    """فایل‌ها را ذخیره و پردازش سه‌مرحله‌ای را در ترد پس‌زمینه شروع می‌کند."""
    missing = [
        FILE_SLOTS[key]["label"]
        for key in FILE_SLOTS
        if FILE_SLOTS[key]["required"] and uploads.get(key) is None
    ]
    if missing:
        raise BudgetValidationError(
            "لطفاً فایل‌های الزامی زیر را بارگذاری کنید: " + "، ".join(missing)
        )

    organization = (options.get("organization") or "").strip() or None
    meeting_context = (options.get("meeting_context") or "").strip() or None
    base_year = (options.get("base_year") or "").strip() or None
    current_year = (options.get("current_year") or "").strip() or None

    workdir = budget_pipeline.make_temp_workdir()
    file_paths: dict[str, Path] = {}
    filenames: dict[str, str] = {}

    try:
        for slot_key, upload in uploads.items():
            if upload is None:
                continue
            content = await upload.read()
            _validate_upload(slot_key, upload, max_upload_bytes, content)
            # نام فایل روی دیسک با نام اصلی ذخیره می‌شود تا گزارش بتواند «اسناد
            # مبنای تحلیل» را با همان نامی که کاربر می‌شناسد بیان کند.
            safe_name = Path(upload.filename or f"{slot_key}.bin").name
            path = workdir / safe_name
            path.write_bytes(content)
            extract_slot = FILE_SLOTS[slot_key]["extract_slot"]
            file_paths[extract_slot] = path
            filenames[extract_slot] = safe_name
    except BudgetValidationError:
        budget_pipeline.cleanup_workdir(workdir)
        raise
    except Exception as exc:  # noqa: BLE001
        budget_pipeline.cleanup_workdir(workdir)
        logger.exception("unexpected error while saving budget uploads")
        raise BudgetValidationError(
            "خطای غیرمنتظره‌ای هنگام آماده‌سازی فایل‌های بارگذاری‌شده رخ داد."
        ) from exc

    config = BudgetConfig.from_mapping({"base_year": base_year, "current_year": current_year})

    job_id, job = manager.create(
        budget_pipeline.new_budget_job,
        workdir=workdir,
        kind=SLUG,
        project_id=project_id,
        run_id=run_id,
        on_finish=on_finish,
    )
    # برچسب‌های خصوصی متادیتای اجرا (خودِ خط پردازش این‌ها را نمی‌خواند) -- فقط
    # برای ثبت در ردیف تاریخچه، مثل سایر کارگاه‌ها.
    job["_organization"] = organization
    job["_meeting_context"] = meeting_context
    job["_base_year"] = base_year
    job["_current_year"] = current_year
    job["_source_filenames"] = list(filenames.values())

    budget_pipeline.start_budget_job(
        job,
        file_paths,
        workdir,
        config=config,
        organization=organization,
        meeting_context=meeting_context,
        report_date=fa_date(utcnow()),
        settings=settings or BudgetLLMSettings.from_env(),
        filenames=filenames,
    )
    manager.watch_lifecycle(job_id, job)
    logger.info(
        "budget-analysis job %s started (project=%s, run=%s, files=%s)",
        job_id,
        project_id,
        run_id,
        ", ".join(filenames.values()),
    )
    return job_id


# ---------------------------------------------------------------------------
# تخمین پیشرفت
# ---------------------------------------------------------------------------
def estimate_progress(job: dict[str, Any]) -> int:
    """درصد پیشرفت تقریبی از روی متن مرحله‌ی جاری (عمومی، مثل سایر کارگاه‌ها)."""
    status = job.get("status")
    if status == "done":
        return 100
    if status == "pending":
        return 0

    stage = job.get("stage") or ""
    hint = budget_pipeline.stage_progress_hint(stage)
    if hint is not None:
        # مرحله‌ی ۱ بعد از پیام جمع‌بندی «... استخراج شد» کمی جلوتر می‌رود.
        if "استخراج شد" in stage and hint == 12:
            return 30
        return hint
    if "در حال شروع" in stage:
        return 3
    return min(92, 6 + 3 * len(job.get("logs", [])))


# ---------------------------------------------------------------------------
# تبدیل خروجی job به پاسخ‌های API
# ---------------------------------------------------------------------------
def _report(job: dict[str, Any]) -> Optional[BudgetAnalysisReport]:
    result = job.get("result") or {}
    report = result.get("report")
    return report if isinstance(report, BudgetAnalysisReport) else None


def build_status_response(job_id: str, job: dict[str, Any]) -> JobStatusResponse:
    status = job.get("status", "pending")
    result = job.get("result") or {}
    return JobStatusResponse(
        job_id=job_id,
        run_id=job.get("_run_id"),
        status=status,
        stage=job.get("stage", ""),
        progress=estimate_progress(job),
        logs=job.get("logs", [])[-25:],
        error=job.get("error"),
        report_ready=has_download(job),
        elapsed_seconds=result.get("elapsed_seconds") if status == "done" else None,
    )


def _location(form: str, section: str, row: str, column: str, year: str = "") -> str:
    parts = [part for part in (form, section, row, column) if part and part.strip()]
    if year and year.strip():
        parts.append(f"سال {year.strip()}")
    return " ← ".join(parts) if parts else ""


def _common_unit(extraction_summary: Optional[list[dict[str, Any]]]) -> Optional[str]:
    """واحد مشترک اسناد (اگر هر دو سند یک واحد داشته باشند) برای سربرگ ستون‌ها."""
    units = {
        entry.get("unit")
        for entry in (extraction_summary or [])
        if isinstance(entry, dict) and entry.get("unit")
    }
    return next(iter(units)) if len(units) == 1 else None


def build_results_response(job_id: str, job: dict[str, Any]) -> JobResultsResponse:
    result = job.get("result") or {}
    report = _report(job)
    organization = job.get("_organization") or ""
    if report is None:
        return JobResultsResponse(
            job_id=job_id,
            organization=organization,
            base_year=job.get("_base_year") or "",
            current_year=job.get("_current_year") or "",
            overall_status="",
            report_error=result.get("error"),
        )

    summary = report.executive_summary
    deviating = report.deviation_rows

    return JobResultsResponse(
        job_id=job_id,
        organization=report.meta.organization or organization,
        base_year=report.meta.base_year,
        current_year=report.meta.current_year,
        unit=_common_unit(result.get("extraction_summary")),
        source_documents=list(report.meta.source_documents),
        overall_status=summary.overall_status,
        executive_summary={
            "وضعیت کلی": summary.overall_status,
            "مهم‌ترین انحراف": summary.top_deviation,
            "مهم‌ترین ریسک مالی": summary.top_financial_risk,
            "مهم‌ترین ریسک ساختاری": summary.top_structural_risk,
            "مهم‌ترین موضوع مرتبط با مأموریت پارک": summary.top_mission_issue,
            "مهم‌ترین تصمیم پیشنهادی": summary.top_recommended_decision,
        },
        status_summary=report.status_counts(),
        matrix_total=len(report.error_matrix),
        deviation_total=len(deviating),
        axis_dashboard=[
            AxisRow(
                axis=row.axis,
                status=row.status,
                importance=row.importance,
                findings_count=row.significant_findings_count,
                management_summary=row.management_summary,
            )
            for row in report.axis_dashboard
        ],
        deviating_rows=[
            MatrixRow(
                axis=row.axis,
                criterion=row.criterion,
                location=_location(row.form, row.section, row.row, row.column, row.comparison_year or row.base_year),
                base_value=row.base_value,
                current_value=row.current_value,
                threshold=row.threshold,
                absolute_deviation=row.absolute_deviation,
                percentage_deviation=row.percentage_deviation,
                status=row.status,
                importance=row.importance,
                evidence=row.evidence,
                action=row.action,
            )
            for row in deviating
        ],
        top_findings=[
            TopFindingRow(
                rank=finding.rank,
                axis=finding.axis,
                criterion=finding.criterion,
                location=finding.location,
                observed_value=finding.observed_value,
                reference_value=finding.reference_value,
                deviation=finding.deviation,
                deviation_unit=finding.deviation_unit,
                importance=finding.importance,
                management_message=finding.management_message,
            )
            for finding in report.top_findings
        ],
        findings=[
            FindingRow(
                number=finding.number,
                title=finding.title,
                subject=finding.subject,
                assessment=finding.assessment,
                management_importance=finding.management_importance,
                risk=finding.risk,
                action=finding.action,
            )
            for finding in report.significant_findings
        ],
        risks=[
            RiskRow(
                description=risk.description,
                approximate_amount=risk.approximate_amount,
                importance=risk.importance,
                nature=risk.nature,
            )
            for risk in report.risks
        ],
        decisions=[
            DecisionRow(
                subject=item.subject, question=item.question, proposed_action=item.proposed_action
            )
            for item in report.items_needing_decision
        ],
        clarifications=[
            ClarificationRow(
                subject=item.subject,
                available_data=item.available_data,
                missing_or_conflicting_data=item.missing_or_conflicting_data,
                location=item.location,
                reason=item.reason,
                required_document=item.required_document,
            )
            for item in report.items_needing_clarification
        ],
        closing_notes=list(report.closing_notes),
        extraction=list(result.get("extraction_summary") or []),
        warnings=list(result.get("warnings") or []),
        report_ready=has_download(job),
        report_error=result.get("error"),
        elapsed_seconds=result.get("elapsed_seconds"),
    )


def get_report_bytes(job: dict[str, Any]) -> tuple[bytes, str]:
    result = job.get("result") or {}
    content = result.get("docx_bytes")
    if not content:
        raise BudgetValidationError("گزارش Word برای این پردازش در دسترس نیست.")
    return content, result.get("docx_filename") or DEFAULT_REPORT_FILENAME


# ---------------------------------------------------------------------------
# ثبت تاریخچه
# ---------------------------------------------------------------------------
def has_download(job: dict[str, Any]) -> bool:
    """آیا همین حالا فایل نتیجه‌ی این job قابل دانلود است؟"""
    return bool((job.get("result") or {}).get("docx_bytes"))


def summary_chips_fa(summary: dict[str, Any]) -> list[str]:
    """برچسب‌های کوتاه خلاصه‌ی نتیجه برای فهرست تاریخچه‌ی پروژه."""
    from api.utils.formatting import fa_number

    chips: list[str] = []
    if summary.get("organization"):
        chips.append(str(summary["organization"]))
    if summary.get("current_year"):
        chips.append(
            f"سال {fa_number(summary.get('current_year'))} / پایه {fa_number(summary.get('base_year'))}"
        )
    if summary.get("deviation_count") is not None:
        chips.append(
            f"{fa_number(summary.get('deviation_count'))} مورد دارای انحراف از "
            f"{fa_number(summary.get('matrix_count'))} معیار"
        )
    if summary.get("finding_count"):
        chips.append(f"{fa_number(summary['finding_count'])} یافته بااهمیت")
    if summary.get("missing_data_count"):
        chips.append(f"{fa_number(summary['missing_data_count'])} معیار فاقد داده")
    if summary.get("report_error"):
        chips.append("گزارش Word تولید نشد")
    return chips


def collect_result(job: dict[str, Any]) -> ResultArtifact:
    """خروجی نهایی اجرا را برای ذخیره در تاریخچه آماده می‌کند.

    فقط *نتیجه* ذخیره می‌شود: فایل Word گزارش + متادیتای شمارشی و خلاصه‌ی
    مدیریتی. فایل‌های ورودی و JSONهای میانی مرحله‌ی ۱/۲ پس از پایان پردازش
    همراه پوشه‌ی کار موقت حذف می‌شوند و هیچ‌کدام در تاریخچه نمی‌آیند.
    """
    result = job.get("result") or {}
    report = _report(job)
    filename = result.get("docx_filename") or DEFAULT_REPORT_FILENAME
    content = result.get("docx_bytes")

    summary: dict[str, Any] = {
        "organization": job.get("_organization") or (report.meta.organization if report else None),
        "base_year": (report.meta.base_year if report else job.get("_base_year")),
        "current_year": (report.meta.current_year if report else job.get("_current_year")),
        "source_filenames": list(job.get("_source_filenames") or []),
        "extraction_methods": list(result.get("extraction_methods") or []),
        "elapsed_seconds": result.get("elapsed_seconds"),
        "warnings": list(result.get("warnings") or [])[:10],
    }

    if report is not None:
        counts = report.status_counts()
        summary.update(
            {
                "matrix_count": len(report.error_matrix),
                "deviation_count": len(report.deviation_rows),
                "finding_count": len(report.significant_findings),
                "risk_count": len(report.risks),
                "decision_count": len(report.items_needing_decision),
                "clarification_count": len(report.items_needing_clarification),
                "missing_data_count": counts.get("فاقد داده کافی", 0),
                "status_counts": counts,
                "executive_summary": report.executive_summary.model_dump(),
            }
        )

    if content:
        summary["result_filename"] = filename
    elif result.get("error"):
        summary["report_error"] = result["error"]

    return ResultArtifact(summary=summary, filename=filename, content=content)
