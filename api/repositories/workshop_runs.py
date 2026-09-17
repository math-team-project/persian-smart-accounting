"""پرس‌وجوهای تاریخچه‌ی اجرای کارگاه‌ها و تنظیمات هر کارگاه در پروژه."""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db.models import WorkshopRun, WorkshopSetting, utcnow


class RunNotFound(Exception):
    """اجرای درخواستی برای این پروژه وجود ندارد."""


# ---------------------------------------------------------------------------
# تاریخچه‌ی اجراها
# ---------------------------------------------------------------------------
def create(session: Session, project_id: int, workshop_type: str) -> WorkshopRun:
    run = WorkshopRun(project_id=project_id, workshop_type=workshop_type, status="pending")
    session.add(run)
    session.commit()
    return run


def get(session: Session, project_id: int, run_id: int) -> Optional[WorkshopRun]:
    run = session.get(WorkshopRun, run_id)
    if run is None or run.project_id != project_id:
        return None
    return run


def list_for_project(
    session: Session, project_id: int, *, limit: Optional[int] = None
) -> list[WorkshopRun]:
    """تاریخچه‌ی پروژه، جدیدترین اجرا اول."""
    stmt = (
        select(WorkshopRun)
        .where(WorkshopRun.project_id == project_id)
        .order_by(WorkshopRun.created_at.desc(), WorkshopRun.id.desc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


def list_unfinished(session: Session, project_id: int) -> list[WorkshopRun]:
    """اجراهای در جریان (یا گیرکرده) که هنوز وضعیت نهایی نگرفته‌اند."""
    stmt = (
        select(WorkshopRun)
        .where(
            WorkshopRun.project_id == project_id,
            WorkshopRun.status.in_(("pending", "running")),
        )
        .order_by(WorkshopRun.created_at.desc(), WorkshopRun.id.desc())
    )
    return list(session.scalars(stmt))


def finish(
    session: Session,
    run_id: int,
    *,
    status: str,
    result_summary: Optional[dict[str, Any]] = None,
    result_file_path: Optional[str] = None,
) -> None:
    """ثبت وضعیت نهایی یک اجرا (تنها جایی که وضعیت ردیف از running خارج می‌شود)."""
    run = session.get(WorkshopRun, run_id)
    if run is None:  # ممکن است پروژه در همین حین حذف شده باشد
        return
    run.status = status
    run.finished_at = utcnow()
    run.result_summary = result_summary
    if result_file_path is not None:
        run.result_file_path = result_file_path
    session.commit()


# ---------------------------------------------------------------------------
# تنظیمات کارگاه در پروژه (همه nullable: مقدار None یعنی «از پیش‌فرض استفاده کن»)
# ---------------------------------------------------------------------------
class _Unset:
    """نشانگر «این فیلد ارسال نشده است» -- با ``None`` (یعنی «پاک کن») اشتباه نشود."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - فقط برای پیام‌های اشکال‌زدایی
        return "<UNSET>"

    def __bool__(self) -> bool:
        return False


UNSET = _Unset()

SettingValue = Optional[str] | _Unset


def get_setting(
    session: Session, project_id: int, workshop_type: str
) -> Optional[WorkshopSetting]:
    return session.scalar(
        select(WorkshopSetting).where(
            WorkshopSetting.project_id == project_id,
            WorkshopSetting.workshop_type == workshop_type,
        )
    )


def list_settings(session: Session, project_id: int) -> dict[str, WorkshopSetting]:
    """تنظیمات ذخیره‌شده‌ی همه‌ی کارگاه‌های یک پروژه، کلید = اسلاگ کارگاه."""
    rows = session.scalars(
        select(WorkshopSetting).where(WorkshopSetting.project_id == project_id)
    ).all()
    return {row.workshop_type: row for row in rows}


def upsert_setting(
    session: Session,
    project_id: int,
    workshop_type: str,
    *,
    api_key: SettingValue = UNSET,
    api_url: SettingValue = UNSET,
    model: SettingValue = UNSET,
    extra_settings: Optional[dict[str, Any]] | _Unset = UNSET,
) -> Optional[WorkshopSetting]:
    """ثبت تنظیمات یک کارگاه در یک پروژه (به‌روزرسانی جزئی).

    هر فیلدی که پاس داده نشود دست‌نخورده می‌ماند و ``None`` یعنی «این مقدار را
    پاک کن تا از پیش‌فرض سامانه استفاده شود». اگر پس از به‌روزرسانی هیچ مقداری
    باقی نماند، ردیف حذف می‌شود تا پایگاه‌داده از ردیف‌های خالی پر نشود.
    خروجی ``None`` یعنی ردیفی باقی نمانده است.
    """
    setting = get_setting(session, project_id, workshop_type)
    if setting is None:
        setting = WorkshopSetting(project_id=project_id, workshop_type=workshop_type)
        session.add(setting)

    if not isinstance(api_key, _Unset):
        setting.api_key = api_key or None
    if not isinstance(api_url, _Unset):
        setting.api_url = api_url or None
    if not isinstance(model, _Unset):
        setting.model = model or None
    if not isinstance(extra_settings, _Unset):
        setting.extra_settings = extra_settings or None

    session.flush()
    if not any((setting.api_key, setting.api_url, setting.model, setting.extra_settings)):
        session.delete(setting)
        session.commit()
        return None

    session.commit()
    return setting

