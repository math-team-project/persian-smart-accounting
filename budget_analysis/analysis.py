"""مرحله‌ی ۲ خط پردازش: تحلیل با مدل زبانی، مقید به JSON ساختاریافته.

جریان کار:

۱) پرامپت (قواعد + داده‌ی مرحله‌ی ۱) ساخته می‌شود.
۲) مدل صدا زده می‌شود و خروجی **باید** منطبق بر ``BudgetAnalysisReport`` باشد.
۳) اگر خروجی نامعتبر بود (مثلاً ردیف دارای انحراف بدون فرم/ردیف، یا وضعیت/اهمیت
   خارج از واژگان مجاز)، **یک‌بار** با متن خطای اعتبارسنجی تکرار می‌شود.
۴) اگر بار دوم هم نامعتبر بود، اجرا با پیام فارسی روشن ناموفق اعلام می‌شود --
   خروجی ناقص هرگز بی‌سروصدا پذیرفته نمی‌شود.

پس از اعتبارسنجی، چند واقعیتِ قطعی که *کد* می‌داند (نام فایل اسناد و سال‌های
تحلیل) روی نتیجه تثبیت می‌شود تا گزارش به داده‌ی ساختگی مدل تکیه نکند.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from pydantic import ValidationError

from budget_analysis.config import BudgetConfig
from budget_analysis.llm import BudgetAnalysisLLM, BudgetLLMError, BudgetLLMSettings
from budget_analysis.prompt import build_messages
from budget_analysis.schemas import BudgetAnalysisReport, ExtractionBundle

logger = logging.getLogger(__name__)

__all__ = ["BudgetAnalysisError", "analyze_budget", "apply_known_facts"]

# نوع callback ای که می‌تواند مرحله‌ی جاری را گزارش کند (job["stage"]).
StageReporter = Callable[[str], None]

_RETRY_NOTE = (
    "\n\n# اصلاح خروجی قبلی\n"
    "خروجی قبلی تو از نظر ساختار/اعتبارسنجی پذیرفته نشد. خطای دقیق:\n"
    "```\n{error}\n```\n"
    "همان تحلیل را با رعایت کامل قواعد، دوباره و این بار فقط به‌صورت یک شیء JSON معتبر "
    "مطابق ساختار تعیین‌شده برگردان. همه‌ی محورها و معیارها باید در ``error_matrix`` ردیف "
    "داشته باشند و هر ردیف دارای انحراف باید فرم و محل داده را ذکر کند."
)


class BudgetAnalysisError(Exception):
    """خطای مرحله‌ی تحلیل -- پیام آن فارسی و قابل‌نمایش به کاربر است."""


def _validate(raw: Any) -> BudgetAnalysisReport:
    if not isinstance(raw, dict):
        raise BudgetAnalysisError(
            "خروجی مدل یک شیء JSON نبود و قابل استفاده نیست."
        )
    return BudgetAnalysisReport.model_validate(raw)


def _format_validation_error(exc: ValidationError) -> str:
    """خطای اعتبارسنجی را به شکل خوانا (و قابل‌فهم برای مدل) خلاصه می‌کند.

    فقط چند خط اول گزارش می‌شود تا پرامپت تکرار بیش از حد بزرگ نشود.
    """
    lines: list[str] = []
    for error in exc.errors()[:8]:
        location = ".".join(str(part) for part in error.get("loc", ()))
        lines.append(f"- {location}: {error.get('msg')}")
    extra = len(exc.errors()) - len(lines)
    if extra > 0:
        lines.append(f"- و {extra} خطای دیگر.")
    return "\n".join(lines) or str(exc)


def apply_known_facts(
    report: BudgetAnalysisReport,
    bundle: ExtractionBundle,
    *,
    organization: Optional[str] = None,
) -> BudgetAnalysisReport:
    """تثبیت واقعیت‌های قطعی روی نتیجه (جای داده‌ی نادرست مدل را نمی‌گیرد، فقط تکمیل می‌کند).

    سال‌های تحلیل، نام اسناد و نام سازمان چیزهایی هستند که *کد* می‌داند؛ بنابراین
    گزارش هرگز به بازگویی درست آن‌ها توسط مدل متکی نمی‌ماند. این تابع در پایان
    مرحله‌ی ۲ و همچنین به‌صورت مستقل در خط پردازش (برای هر پیاده‌سازی تزریق‌شده)
    فراخوانی می‌شود.
    """
    if bundle.base_year:
        report.meta.base_year = bundle.base_year
    if bundle.current_year:
        report.meta.current_year = bundle.current_year
    if organization and organization.strip():
        report.meta.organization = organization.strip()
    filenames = [document.filename for document in bundle.documents if document.filename]
    if filenames:
        report.meta.source_documents = filenames

    report.top_findings.sort(key=lambda item: (item.rank is None, item.rank or 0))
    for index, finding in enumerate(report.top_findings, start=1):
        if finding.rank is None or finding.rank <= 0:
            finding.rank = index
    return report


def analyze_budget(
    bundle: ExtractionBundle,
    config: Optional[BudgetConfig] = None,
    *,
    organization: Optional[str] = None,
    meeting_context: Optional[str] = None,
    settings: Optional[BudgetLLMSettings] = None,
    llm: Optional[BudgetAnalysisLLM] = None,
    report_stage: Optional[StageReporter] = None,
) -> BudgetAnalysisReport:
    """تحلیل مرحله‌ی ۲ با اعتبارسنجی سختگیرانه و یک تلاش مجدد.

    Raises:
        BudgetAnalysisError: در صورت شکست ارتباط، خروجی نامعتبر در هر دو تلاش، یا
            نبود داده‌ی قابل‌تحلیل.
    """
    config = config or BudgetConfig()
    reporter = report_stage or (lambda _message: None)
    client = llm or BudgetAnalysisLLM(settings)

    if not bundle.documents:
        raise BudgetAnalysisError(
            "هیچ سند قابل‌تحلیلی برای پردازش وجود ندارد؛ لطفاً هر دو سند بودجه را بارگذاری کنید."
        )

    messages: list[dict[str, str]] = build_messages(
        bundle,
        config,
        organization=organization,
        meeting_context=meeting_context,
    )

    reporter("مرحله ۲ از ۳: تحلیل معیارها، ساخت ماتریس خطادهی و یافته‌های مدیریتی با هوش مصنوعی...")
    last_error: Optional[str] = None

    for attempt in (1, 2):
        if attempt == 2:
            reporter("مرحله ۲ از ۳: خروجی مدل ناقص بود؛ تکرار تحلیل با اصلاح خطا...")
        try:
            raw = client.complete_json(messages)
        except BudgetLLMError as exc:
            # خطای ارتباط/سرویس با تکرار بیهوده‌ی ساختاری بهتر نمی‌شود؛ همان پیام
            # روشن سرویس به کاربر برمی‌گردد.
            raise BudgetAnalysisError(str(exc)) from exc

        try:
            report = _validate(raw)
        except BudgetAnalysisError as exc:
            last_error = str(exc)
            logger.warning("budget analysis attempt %d: %s", attempt, last_error)
        except ValidationError as exc:
            last_error = _format_validation_error(exc)
            logger.warning(
                "budget analysis attempt %d validation failed: %s", attempt, last_error
            )
        else:
            logger.info(
                "budget analysis succeeded on attempt %d (%d matrix rows, %d findings)",
                attempt,
                len(report.error_matrix),
                len(report.significant_findings),
            )
            return apply_known_facts(report, bundle, organization=organization)

        if attempt == 1:
            messages = [
                messages[0],
                {
                    "role": "user",
                    "content": messages[1]["content"] + _RETRY_NOTE.format(error=last_error),
                },
            ]

    raise BudgetAnalysisError(
        "خروجی مدل هوش مصنوعی پس از دو تلاش از نظر ساختاری معتبر نبود و پردازش متوقف شد. "
        f"جزئیات خطا: {last_error}"
    )
