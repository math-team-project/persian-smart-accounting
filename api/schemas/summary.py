"""مدل‌های Pydantic برای پاسخ‌های endpoint های کارگاه خلاصه‌سازی گزارش حسابرسی.

ساختار این فایل عمداً موازی با ``api/schemas/checklist.py`` نگه داشته شده تا
هر دو کارگاه از یک الگوی یکسان (job_id/status/stage/progress/logs/error)
پیروی کنند؛ ``StartJobResponse`` بین هر دو مشترک است (نگاه کنید
``api/schemas/common.py``).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

# ``JobStatus`` و ``StartJobResponse`` از common.py مجددامدوری می‌شوند (منبع واحد
# برای هر دو کارگاه) تا با api/schemas/checklist.py هماهنگ بماند.
from api.schemas.common import JobStatus, StartJobResponse  # noqa: F401  (برای دسترسی مستقیم از همین ماژول)


class JobStatusResponse(BaseModel):
    job_id: str
    # شناسه‌ی ردیف تاریخچه‌ی همین اجرا (برای ساخت لینک دانلود نتیجه).
    run_id: Optional[int] = None
    status: JobStatus
    stage: str
    progress: int
    logs: list[str] = []
    error: Optional[str] = None
    summary_ready: bool = False
    elapsed_seconds: Optional[float] = None


class JobResultsResponse(BaseModel):
    job_id: str
    summary_markdown: str
    summary_html: str
    warnings: list[str] = []
    source_filename: str = ""
    elapsed_seconds: Optional[float] = None
