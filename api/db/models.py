"""مدل‌های ORM داشبورد: کاربر، پروژه، اجرای کارگاه و تنظیمات کارگاه.

شکل جدول‌ها همان است که در طرح این فاز پیشنهاد شده:

* ``users``            -- کاربران داشبورد (نام کاربری + هش رمز).
* ``projects``         -- پروژه‌های هر کاربر.
* ``workshop_runs``    -- تاریخچه‌ی اجرای کارگاه‌ها: فقط *خروجی* (فایل نتیجه و
  خلاصه‌ی متادیتای نتیجه)؛ هیچ‌گاه محتوای فایل ورودی یا مسیر فایل ورودی.
* ``workshop_settings`` -- تنظیمات اختیاری هر کارگاه در هر پروژه (کلید/آدرس/مدل
  LLM و ...). همه‌ی ستون‌ها nullable هستند: مقدار NULL یعنی «از پیش‌فرض استفاده کن».
  مصرف واقعی این جدول در فاز بعد (تنظیمات) است؛ در این فاز فقط ساخته می‌شود.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from api.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    projects: Mapped[list["Project"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="projects")
    runs: Mapped[list["WorkshopRun"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    settings: Mapped[list["WorkshopSetting"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class WorkshopRun(Base):
    """یک اجرای کارگاه = یک ردیف تاریخچه.

    این ردیف عمداً هیچ ستونی برای فایل‌های ورودی ندارد: نه محتوا، نه مسیر. تنها
    artifact ذخیره‌شده ``result_file_path`` است (مسیر نسبی فایل نتیجه در
    ``settings.results_dir``) به‌همراه ``result_summary`` که فقط متادیتای کوچک
    نتیجه (درصد تطابق، تعداد موارد، نام فایل مبدأ و...) را نگه می‌دارد.
    """

    __tablename__ = "workshop_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    workshop_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    result_summary: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    result_file_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    project: Mapped[Project] = relationship(back_populates="runs")


class WorkshopSetting(Base):
    __tablename__ = "workshop_settings"
    __table_args__ = (
        UniqueConstraint("project_id", "workshop_type", name="uq_workshop_setting"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    workshop_type: Mapped[str] = mapped_column(String(64))
    api_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    api_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extra_settings: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    project: Mapped[Project] = relationship(back_populates="settings")
