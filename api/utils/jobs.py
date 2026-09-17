"""کمک‌تابع مشترک روترها برای بازیابی یک job از ``job_manager`` یا بازگرداندن
خطای HTTP 404 با پیام فارسی -- مستقل از اینکه job متعلق به کدام کارگاه
(چک‌لیست، خلاصه‌سازی گزارش یا تحلیل بودجه) است.

job ها دو برچسب مالکیت دارند و بازیابی **هر دو** را بررسی می‌کند:

* ``_project_id`` -- تا job یک پروژه از مسیر پروژه‌ی دیگر (حتی برای همان کاربر)
  قابل دسترسی نباشد،
* ``_kind`` (اسلاگ کارگاه) -- تا یک job از مسیر کارگاه *دیگر* خوانده نشود. بدون
  این بررسی، شناسه‌ی یک job چک‌لیست از مسیر تحلیل بودجه قابل خواندن بود و پاسخ هم
  با منطق *همان کارگاه اشتباه* ساخته می‌شد (پیشرفت و «آماده‌بودن گزارش» بر پایه‌ی
  دیکشنری job دیگری محاسبه می‌شد) -- یعنی همان نشتی حالت بین کارگاه‌ها.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException

from api.jobs.job_manager import job_manager

_NOT_FOUND_DETAIL = "پردازش مورد نظر یافت نشد (ممکن است منقضی شده باشد)."


def get_job_or_404(
    job_id: str,
    *,
    project_id: Optional[int] = None,
    workshop_slug: Optional[str] = None,
) -> dict[str, Any]:
    """job را با بررسی مالکیت برمی‌گرداند؛ در غیر این صورت 404 فارسی.

    هر دو بررسی ناموفق عمداً همان 404 یکسان را برمی‌گردانند تا هیچ اطلاعاتی
    درباره‌ی وجود job در پروژه/کارگاه دیگر لو نرود.
    """
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)
    if project_id is not None and job.get("_project_id") != project_id:
        raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)
    if workshop_slug is not None and job.get("_kind") != workshop_slug:
        raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)
    return job
