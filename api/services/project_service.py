"""کسب‌وکار حذف پروژه: پایگاه‌داده + فایل‌های نتیجه + job های در حافظه.

«حذف پروژه» باید یک عمل کامل و بدون باقی‌مانده باشد: هیچ ردیف یتیمی در پایگاه‌داده
و هیچ فایل یتیمی روی دیسک باقی نماند. این تابع همان ترتیبی را اجرا می‌کند که این
تضمین را می‌دهد:

    ۱) job های در حافظه‌ی همان پروژه از رجیستری خارج می‌شوند (تا هیچ endpoint ای
       بعد از حذف، وضعیت پروژه‌ی حذف‌شده را برنگرداند؛ ترد پردازش در پس‌زمینه
       مستقل است و کار خودش را تمام می‌کند، اما چون ردیف تاریخچه دیگر وجود ندارد،
       ثبت نتیجه‌اش بی‌اثر و بی‌خطر است)،
    ۲) ردیف‌های ``workshop_runs`` و ``workshop_settings`` و خود پروژه حذف می‌شوند،
    ۳) کل پوشه‌ی نتیجه‌های همان پروژه روی دیسک پاک می‌شود (این کار حتی فایل‌های
       یتیم احتمالی را هم از بین می‌برد، نه فقط فایل‌های شناخته‌شده در پایگاه‌داده).

برگشت‌پذیر نیست -- به همین دلیل UI یک مرحله‌ی تأیید صریح دارد.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from api import storage
from api.db.models import Project
from api.jobs.job_manager import job_manager
from api.repositories import projects as projects_repo

logger = logging.getLogger(__name__)


def delete_project(session: Session, project: Project, *, manager=job_manager) -> dict[str, int]:
    project_id = project.id
    removed_jobs = manager.delete_for_project(project_id)
    summary = projects_repo.delete(session, project)
    paths = summary["result_paths"]
    storage.delete_project_files(project_id)

    logger.info(
        "project %s deleted (jobs=%d, runs=%d, settings=%d, result_files=%d)",
        project_id,
        removed_jobs,
        summary["runs"],
        summary["settings"],
        len(paths),
    )
    return {
        "jobs": removed_jobs,
        "runs": summary["runs"],
        "settings": summary["settings"],
        "result_files": len(paths),
    }
