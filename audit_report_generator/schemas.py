"""ساختارهای داده‌ای پکیج.

این ماژول عمداً به ``dataclasses`` استاندارد پایتون بسنده کرده تا پکیج
وابستگی اضافه (مثل pydantic) نداشته باشد؛ اما تمام اعتبارسنجی‌های لازم را
در ``data_loader.py`` انجام می‌دهیم.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ChecklistItem:
    """نمایش یک سوالِ دارای «عدم تطابق» از چک‌لیست حسابرسی.

    فقط فیلدهایی که برای تولید گزارش لازم است نگه داشته می‌شود؛ فیلدهای خام
    اضافیِ ورودی در ``raw`` نگهداری می‌شوند تا در صورت نیاز در دسترس باشند.
    """

    question_id: str
    question_text: str
    message: str = ""
    question_purpose: str = ""
    status: str = "FALSE"
    key_figures: List[Dict[str, str]] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LoadedInput:
    """خروجی مرحله بارگذاری/اعتبارسنجی ورودی‌ها، آماده برای ساخت پرامپت."""

    doc_text: str
    has_audit_report: bool = True
    all_items_count: Optional[int] = None
    true_count: Optional[int] = None
    false_count: Optional[int] = None
    error_count: Optional[int] = None
    manual_count: Optional[int] = None
    compliance_rate: Optional[float] = None
    non_compliant_items: List[ChecklistItem] = field(default_factory=list)


@dataclass
class ReportItem:
    """خروجیِ تولیدشده توسط LLM برای هر مورد عدم تطابق، آماده درج در گزارش Word."""

    question_id: str
    title: str
    summary: str
    committee_focus: str
    mentioned_in_audit_report: bool = False
    severity: str = "متوسط"  # کم / متوسط / بالا


@dataclass
class AuditFinding:
    """یک نکته‌ی مهم که مستقیماً از متن گزارش حسابرسی مستقل استخراج شده و در
    چک‌لیست خودکار پوشش داده نشده است (مثل بند شرط/تاکید، ابهام در تداوم فعالیت،
    یادداشت‌های توضیحی مهم، محدودیت رسیدگی و ...)."""

    title: str
    summary: str
    committee_focus: str
    severity: str = "متوسط"  # کم / متوسط / بالا
    source_hint: str = ""  # اشاره کوتاه به بخش/یادداشت مرتبط در گزارش حسابرسی (اختیاری)

