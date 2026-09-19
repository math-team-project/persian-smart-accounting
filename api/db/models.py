"""مدل‌های ORM داشبورد: کاربر، پروژه، اجرای کارگاه و تنظیمات کارگاه.

شکل جدول‌ها همان است که در طرح این فاز پیشنهاد شده:

* ``users``            -- کاربران داشبورد (نام کاربری + هش رمز).
* ``projects``         -- پروژه‌های هر کاربر.
* ``workshop_runs``    -- تاریخچه‌ی اجرای کارگاه‌ها: فقط *خروجی* (فایل نتیجه و
  خلاصه‌ی متادیتای نتیجه)؛ هیچ‌گاه محتوای فایل ورودی یا مسیر فایل ورودی.
* ``workshop_settings`` -- تنظیمات اختیاری هر کارگاه در هر پروژه (کلید/آدرس/مدل
  LLM و ...). همه‌ی ستون‌ها nullable هستند: مقدار NULL یعنی «از پیش‌فرض استفاده کن».
  مصرف واقعی این جدول در فاز بعد (تنظیمات) است؛ در این فاز فقط ساخته می‌شود.
* ``chat_sessions``    -- فقط *متادیتای* گفتگوهای کارگاه «چت‌بات مالی»: یک عنوان و
  دو زمان. **هیچ جدول پیام‌ها و هیچ متنی از گفتگو ذخیره نمی‌شود** -- باز کردن
  دوباره‌ی یک گفتگوی قدیمی، فهرست پیام‌ها را برنمی‌گرداند (عمداً؛ نگاه کنید به
  ``ChatSession``).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from api.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_kb_id() -> str:
    """شناسه‌ی عمومی/بیرونی یک پایگاه‌دانش -- همین رشته نام پوشه‌ی روی دیسک هم هست."""
    return str(uuid.uuid4())


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
    # جدیدترین پایگاه‌دانش «آماده» (status="ready") این پروژه -- تنها منبعی که
    # مصرف‌کننده‌های آینده (مثل کارگاه دستیار مالی) باید بخوانند؛ هرگز نباید خودشان
    # جدول ``knowledge_bases`` را برای «حدس‌زدن جدیدترین» پرس‌وجو کنند.
    # ``use_alter`` چرخه‌ی کلید خارجی projects<->knowledge_bases را می‌شکند.
    latest_ready_kb_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey(
            "knowledge_bases.id", ondelete="SET NULL", use_alter=True, name="fk_projects_latest_ready_kb"
        ),
        nullable=True,
    )

    user: Mapped[User] = relationship(back_populates="projects")
    runs: Mapped[list["WorkshopRun"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    settings: Mapped[list["WorkshopSetting"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    knowledge_bases: Mapped[list["KnowledgeBase"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        foreign_keys="KnowledgeBase.project_id",
    )
    chat_sessions: Mapped[list["ChatSession"]] = relationship(
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


class KnowledgeBase(Base):
    """یک نسخه‌ی نسک-پایگاه‌دانش (RAG index) متعلق به یک کاربر/پروژه.

    قرارداد تکوین/نسخه‌بندی: هر بار که فایل‌های پروژه دوباره آپلود/کارگاه
    چک‌لیست اجرا شود، یک ردیف جدید (با یک UUID جدید و یک پوشه‌ی Chroma جدید روی
    دیسک) ساخته می‌شود -- هیچ نسخه‌ی قبلی هرگز بازنویسی/جایگذینی نمی‌شود. ستون
    ``id`` یک UUID رشته‌ای است (نه عدد صحیح خودکارافزاینده) چون همین مقدار نام
    پوشه‌ی روی دیسک هم هست و باید جهانی یکتا باشد.
    """

    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_kb_id)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # یک پایگاه‌دانش متعلق به یک کاربر مشخص **و** یک پروژه مشخص است --
    # هرگز اجازه نمی‌دهیم یک کاربر (حتی در همان پروژه) پایگاه‌دانش کاربر
    # دیگری را بخواند (این اپلیکیشن اشتراک پروژه ندارد).
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    embedding_model: Mapped[str] = mapped_column(String(255))
    embedding_device: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="indexing", index=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    chroma_collection_name: Mapped[str] = mapped_column(String(255))
    # مسیر نسبی (نسبت به ``PSA_VECTOR_STORE_ROOT``) تا جابه‌جایی پوشه‌ی داده‌ها ردیف‌های
    # قبلی را خراب نکند.
    chroma_persist_dir: Mapped[str] = mapped_column(Text)

    project: Mapped[Project] = relationship(
        back_populates="knowledge_bases", foreign_keys=[project_id]
    )
    files: Mapped[list["KBFile"]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )


class KBFile(Base):
    """یک فایل ورودی که در ساخت یک پایگاه‌دانش استفاده شده (فقط تشخیص/ردیابی‌پذیری)."""

    __tablename__ = "kb_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    # کلید منطقی مشابه آنچه در ``file_registry`` استفاده می‌شود (مثل "taidiyeh").
    file_key: Mapped[str] = mapped_column(String(128))
    original_filename: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    sheet_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="files")


class ChatSession(Base):
    """یک «گفتگوی نام‌دار» کارگاه چت‌بات مالی -- فقط متادیتا، بدون هیچ پیامی.

    این جدول عمداً از یک اپ چت معمولی فاصله می‌گیرد: هیچ جدول ``chat_messages``ی
    وجود ندارد و هیچ متن گفتگویی (نه پرسش کاربر، نه پاسخ مدل) روی دیسک یا در
    پایگاه‌داده نوشته نمی‌شود. فقط عنوان و دو زمان نگه داشته می‌شوند تا کاربر
    بتواند فهرست گفتگوهایش را ببیند، بین‌شان جابه‌جا شود و هرکدام را پاک کند.

    نتیجه‌ی عملی و **عمدی**: باز کردن دوباره‌ی یک گفتگوی قدیمی، یک گفتگوی خالی
    نشان می‌دهد (فقط عنوانش برمی‌گردد) -- محتوای پیام‌ها هرگز بازیابی نمی‌شود.
    زمینه‌ی چندنوبتی (multi-turn) فقط در حافظه‌ی مرورگر و در طول همان گفتگوی
    بازِ در حال استفاده زنده است (نگاه کنید به
    ``api/services/financial_chatbot_service.py`` و ``web/static/js/financial_chatbot.js``).

    جایگاه امنیتی: مثل ``KnowledgeBase``، یک گفتگو متعلق به یک ``user_id`` **و**
    یک ``project_id`` مشخص است؛ هیچ گفتگویی با شناسه‌اش از پروژه/کاربر دیگر
    خوانده نمی‌شود (همان الگوی «۴۰۴ نه ۴۰۳» در ``api/repositories/chat_sessions.py``).

    ``updated_at`` هنگام هر پرسش در همان گفتگو تازه می‌شود، بنابراین فهرست
    گفتگوها (جدیدترین اول) با «آخرین استفاده» مرتب می‌شود، نه فقط با زمان ساخت.
    """

    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    project: Mapped[Project] = relationship(back_populates="chat_sessions")
