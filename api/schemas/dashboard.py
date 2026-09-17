"""مدل‌های Pydantic برای پاسخ‌های API داشبورد/پروژه‌ها."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from api.schemas.common import JobStatus


class RunPanelItem(BaseModel):
    """یک ردیف از پنل «کارهای در جریان» یا تاریخچه (وضعیت زنده‌ی هر اجرا)."""

    run_id: int
    job_id: Optional[str] = None
    workshop: str
    workshop_name_fa: str
    workshop_icon: str = "circle"
    status: JobStatus | str
    stage: str = ""
    progress: int = 0
    error: Optional[str] = None
    created_at: Optional[str] = None
    finished_at: Optional[str] = None
    result_summary: Optional[dict[str, Any]] = None
    download_url: Optional[str] = None
    download_ready: bool = False
    workshop_url: Optional[str] = None


class RunPanelResponse(BaseModel):
    project_id: int
    jobs: list[RunPanelItem]


class ProjectItem(BaseModel):
    id: int
    name: str
    created_at: Optional[str] = None
    run_count: int = 0
