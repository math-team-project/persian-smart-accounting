"""
audit_report_generator
=======================
یک پکیج پایتون برای تولید خودکار «گزارش موارد عدم تطابق چک‌لیست حسابرسی»
جهت ارائه در جلسه کمیسیون، با استفاده از یک مدل زبانی (LLM) از طریق کتابخانه openai.

نحوه استفاده سریع
------------------
    from audit_report_generator import LLMConfig, generate_committee_report

    config = LLMConfig(
        api_key="sk-...",
        base_url="https://api.openai.com/v1",   # هر ارائه‌دهنده سازگار با OpenAI
        model="gpt-4o-mini",
    )

    generate_committee_report(
        doc_text=my_doc_text,          # متن استخراج‌شده از فایل گزارش حسابرسی (doc/docx)
        checklist_json=my_json_data,   # خروجی جیسون (کل چک‌لیست یا فقط موارد FALSE)
        config=config,
        output_path="output/گزارش_کمیسیون.docx",
    )
"""

from .config import LLMConfig
from .schemas import ChecklistItem, ReportItem, AuditFinding, LoadedInput
from .exceptions import (
    AuditReportError,
    InputValidationError,
    LLMGenerationError,
    DocxBuildError,
)
from .report_generator import generate_committee_report, ReportGenerator

__all__ = [
    "LLMConfig",
    "ChecklistItem",
    "ReportItem",
    "AuditFinding",
    "LoadedInput",
    "AuditReportError",
    "InputValidationError",
    "LLMGenerationError",
    "DocxBuildError",
    "generate_committee_report",
    "ReportGenerator",
]

__version__ = "1.0.0"
