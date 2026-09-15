"""مدل‌های Pydantic برای پاسخ‌های endpoint های کارگاه چک‌لیست حسابرسی."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

# ``JobStatus`` و ``StartJobResponse`` از common.py مجددامدوری می‌شوند (منبع واحد
# برای هر دو کارگاه) تا با api/schemas/summary.py هماهنگ بماند.
from api.schemas.common import JobStatus, StartJobResponse  # noqa: F401  (برای سازگاری با واردکنندگان قبلی روتر)


class JobSummary(BaseModel):
    """معادل ساده‌شده‌ی خروجی ``pipeline.summarize_checklist`` برای نمایش سریع."""

    total: int
    true_count: int
    false_count: int
    error_count: int
    manual_count: int
    compliance_rate: float


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    stage: str
    progress: int
    logs: list[str] = []
    error: Optional[str] = None
    summary: Optional[JobSummary] = None
    report_ready: bool = False
    elapsed_seconds: Optional[float] = None


class ChecklistItem(BaseModel):
    question_id: str
    question_text: str
    question_purpose: str
    is_evaluable: bool
    status: str
    message: str
    evaluation_condition: Optional[str] = None
    condition_breakdown: list[dict[str, Any]] = []
    extracted_data: list[dict[str, Any]] = []


class JobResultsResponse(BaseModel):
    job_id: str
    summary: JobSummary
    warnings: list[str] = []
    items: list[ChecklistItem]
    report_ready: bool = False
    committee_report_error: Optional[str] = None
    used_audit_report_text: bool = False
