"""مدل‌های Pydantic مشترک بین کارگاه‌های مختلف (چک‌لیست و خلاصه‌سازی گزارش)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# منبع واحد برای مقادیر معتبر وضعیت job -- هر دو کارگاه (چک‌لیست و خلاصه‌سازی
# گزارش) دقیقاً همین چهار مقدار را تولید می‌کنند (نگاه کنید
# ``pipeline.new_checklist_job`` / ``audit_pipeline.new_audit_job`` و
# ``JobManager.create`` که وضعیت اولیه را به "pending" نرمالایز می‌کند).
JobStatus = Literal["pending", "running", "done", "error"]


class StartJobResponse(BaseModel):
    job_id: str
