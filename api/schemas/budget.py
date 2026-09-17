"""مدل‌های Pydantic پاسخ‌های endpoint های کارگاه «تحلیل بودجه».

ساختار با دو کارگاه دیگر موازی است (``job_id``/``status``/``stage``/``progress``/
``logs``/``error``/``run_id``) تا صفحه‌ی پروژه و جاوااسکریپت بتوانند با یک قرارداد
ثابت با هر سه کارگاه کار کنند. بخش نتایج، خروجی ساختاریافته‌ی مرحله‌ی ۲ را برای
نمایش در مرورگر خلاصه می‌کند (قالب نهایی همان فایل Word است).
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from api.schemas.common import JobStatus, StartJobResponse  # noqa: F401  (بازصادرات عمدی)


class JobStatusResponse(BaseModel):
    job_id: str
    run_id: Optional[int] = None
    status: JobStatus
    stage: str
    progress: int
    logs: list[str] = []
    error: Optional[str] = None
    # آیا گزارش Word همین حالا آماده‌ی دانلود است؟
    report_ready: bool = False
    elapsed_seconds: Optional[float] = None


class AxisRow(BaseModel):
    """یک ردیف داشبورد محورهای پایش."""

    axis: str
    status: str
    importance: str
    findings_count: int
    management_summary: str


class MatrixRow(BaseModel):
    """یک ردیف ماتریس خطادهی (فقط موارد دارای انحراف در نمایش خلاصه)."""

    axis: str
    criterion: str
    location: str
    base_value: Optional[Any] = None
    current_value: Optional[Any] = None
    threshold: Optional[Any] = None
    absolute_deviation: Optional[Any] = None
    percentage_deviation: Optional[Any] = None
    status: str
    importance: str
    evidence: str
    action: str


class TopFindingRow(BaseModel):
    rank: Optional[int] = None
    axis: str
    criterion: str
    location: str
    observed_value: Optional[Any] = None
    reference_value: Optional[Any] = None
    deviation: Optional[Any] = None
    deviation_unit: str
    importance: str
    management_message: str


class FindingRow(BaseModel):
    number: Optional[Any] = None
    title: str
    subject: str
    assessment: str
    management_importance: str
    risk: str
    action: str


class RiskRow(BaseModel):
    description: str
    approximate_amount: Optional[Any] = None
    importance: str
    nature: str


class DecisionRow(BaseModel):
    subject: str
    question: str
    proposed_action: str


class ClarificationRow(BaseModel):
    subject: str
    available_data: str
    missing_or_conflicting_data: str
    location: str
    reason: str
    required_document: str


class JobResultsResponse(BaseModel):
    """نتیجه‌ی ساختاریافته‌ی یک اجرای تمام‌شده (نمایش مرورگری، نه سند نهایی)."""

    job_id: str
    organization: str
    base_year: str
    current_year: str
    unit: Optional[str] = None
    source_documents: list[str] = []
    overall_status: str
    executive_summary: dict[str, str] = {}
    status_summary: dict[str, int] = {}
    matrix_total: int = 0
    deviation_total: int = 0
    axis_dashboard: list[AxisRow] = []
    deviating_rows: list[MatrixRow] = []
    top_findings: list[TopFindingRow] = []
    findings: list[FindingRow] = []
    risks: list[RiskRow] = []
    decisions: list[DecisionRow] = []
    clarifications: list[ClarificationRow] = []
    closing_notes: list[str] = []
    extraction: list[dict[str, Any]] = []
    warnings: list[str] = []
    report_ready: bool = False
    report_error: Optional[str] = None
    elapsed_seconds: Optional[float] = None
