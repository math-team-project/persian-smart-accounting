"""پرس‌وجوهای مربوط به پروژه‌ها (همراه با شمارش اجراها برای صفحه‌ی داشبورد)."""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.db.models import Project, WorkshopRun, WorkshopSetting


def list_for_user(session: Session, user_id: int) -> list[tuple[Project, int]]:
    """پروژه‌های یک کاربر (جدیدترین اول) همراه با تعداد اجراهای هر پروژه."""
    run_count = func.count(WorkshopRun.id)
    rows = session.execute(
        select(Project, run_count)
        .outerjoin(WorkshopRun, WorkshopRun.project_id == Project.id)
        .where(Project.user_id == user_id)
        .group_by(Project.id)
        .order_by(Project.created_at.desc(), Project.id.desc())
    ).all()
    return [(project, int(count)) for project, count in rows]


def get(session: Session, project_id: int) -> Optional[Project]:
    return session.get(Project, project_id)


def get_owned(session: Session, project_id: int, user_id: int) -> Optional[Project]:
    """پروژه فقط در صورتی برگردانده می‌شود که متعلق به همین کاربر باشد.

    پروژه‌ی متعلق به کاربر دیگر «پیدا نشد» (404) در نظر گرفته می‌شود، نه «ممنوع»
    (403) -- تا وجود/عدم وجود پروژه‌های سایر کاربران لو نرود.
    """
    project = session.get(Project, project_id)
    if project is None or project.user_id != user_id:
        return None
    return project


def create(session: Session, user_id: int, name: str) -> Project:
    project = Project(user_id=user_id, name=name.strip())
    session.add(project)
    session.commit()
    return project


def count_runs(session: Session, project_id: int) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(WorkshopRun).where(WorkshopRun.project_id == project_id)
        )
        or 0
    )


def delete(session: Session, project: Project) -> dict[str, Any]:
    """حذف پروژه و **همه‌ی** داده‌های وابسته در پایگاه‌داده.

    ردیف‌های ``workshop_runs`` و ``workshop_settings`` از طریق
    ``ON DELETE CASCADE`` (و cascade در سطح ORM) با حذف پروژه پاک می‌شوند؛
    SQLite بدون ``PRAGMA foreign_keys=ON`` این کلیدهای خارجی را اجرا نمی‌کند،
    به همین دلیل در ``api/db/base.py`` هنگام هر اتصال فعال می‌شود.

    خروجی: ``{"result_paths": [...], "runs": n, "settings": n}`` -- مسیرهای نسبی
    فایل‌های نتیجه (تا لایه‌ی سرویس آن‌ها را از روی دیسک هم پاک کند) به‌همراه
    شمار واقعی ردیف‌های حذف‌شده، برای گزارش دقیق در لاگ.
    """
    result_paths = [
        path
        for (path,) in session.execute(
            select(WorkshopRun.result_file_path).where(
                WorkshopRun.project_id == project.id,
                WorkshopRun.result_file_path.is_not(None),
            )
        ).all()
    ]

    # حذف صریح ردیف‌های وابسته، صرف‌نظر از اینکه cascade در سطح پایگاه‌داده فعال
    # باشد یا نه -- تا هرگز ردیف یتیم باقی نماند. (ردیف‌های تنظیمات شامل کلیدهای
    # رمزشده‌ی API هم می‌شوند: با حذف پروژه، هیچ کلیدی باقی نمی‌ماند.)
    run_rows = session.query(WorkshopRun).filter(WorkshopRun.project_id == project.id).delete(
        synchronize_session=False
    )
    setting_rows = session.query(WorkshopSetting).filter(
        WorkshopSetting.project_id == project.id
    ).delete(synchronize_session=False)
    session.delete(project)
    session.commit()
    return {"result_paths": result_paths, "runs": int(run_rows), "settings": int(setting_rows)}
