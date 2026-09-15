"""کمک‌تابع مشترک روترها برای بازیابی یک job از ``job_manager`` یا بازگرداندن
خطای HTTP 404 با پیام فارسی -- مستقل از اینکه job متعلق به کدام کارگاه
(چک‌لیست یا خلاصه‌سازی گزارش) است.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from api.jobs.job_manager import job_manager


def get_job_or_404(job_id: str) -> dict[str, Any]:
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="پردازش مورد نظر یافت نشد (ممکن است منقضی شده باشد).",
        )
    return job
