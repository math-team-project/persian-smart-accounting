"""مدل‌های ORM داشبورد: کاربر، پروژه، اجرای کارگاه و تنظیمات کارگاه.

شکل جدول‌ها همان است که در طرح این فاز پیشنهاد شده:

* ``users``            -- کاربران داشبورد (نام کاربری + هش رمز).
* ``projects``         -- پروژه‌های هر کاربر.
* ``workshop_runs``    -- تاریخچه‌ی اجرای کارگاه‌ها: فقط *خروجی* (فایل نتیجه و
  خلاصه‌ی متادیتای نتیجه)؛ هیچ‌گاه محتوای فایل ورودی یا مسیر فایل ورودی.
* ``workshop_settings`` -- تنظیمات اختیاری هر کارگاه در هر پروژه (کلید/آدرس/مدل
  LLM و ...). همه‌ی ستون‌ها nullable هستند: مقدار NULL یعنی «از پیش‌فرض استفاده کن».
  مصرف واقعی این جدول در فاز بعد (تنظیمات) است؛ در این فاز فقط ساخته می‌شود.
* ``chat_sessions``    -- گفتگوهای نام‌دار کارگاه «چت‌بات مالی»: یک عنوان و دو زمان
  (متادیتا).
* ``chat_messages``    -- *محتوای* گفتگوها: هر پرسش کاربر و هر پاسخ دستیار، به‌همراه
  وضعیت تولید پاسخ (``pending``/``complete``/``failed``)، منابع استناد و پیام خطا.
  باز کردن دوباره‌ی یک گفتگوی قدیمی، واقعاً تاریخچه‌ی پیام‌هایش را برمی‌گرداند.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from api.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    """شناسه‌ی عمومی/بیرونی یک ردیف -- یک UUID رشته‌ای جهانی‌یکتا.

    هم ``knowledge_bases.id`` و هم ``chat_sessions.id``/``chat_messages.id`` از همین
    تابع می‌آیند. یکسان‌بودن الگوی «شناسه‌ی رشته‌ای UUID به‌جای عدد خودکارافزاینده»
    در این دو بخش عمدی است: شناسه‌ی پایگاه‌دانش نام پوشه‌ی روی دیسک هم هست و
    شناسه‌ی گفتگو/پیام کلید جداسازی «کاربر/پروژه‌ی دیگر» را قابل‌حمل می‌کند.
    """
    return str(uuid.uuid4())


# نام قدیمی‌تر همین تابع (فقط برای پایگاه‌دانش) -- نگه داشته شده تا خواننده‌ی کد
# قدیمی گیج نشود؛ هر دو یک مقدار تولید می‌کنند.
def new_kb_id() -> str:
    return new_uuid()


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
    """یک «گفتگوی نام‌دار» کارگاه چت‌بات مالی -- متادیتای گفتگو.

    این جدول فقط عنوان و دو زمان را نگه می‌دارد؛ **محتوای گفتگو در جدول
    ``chat_messages`` است** (هر پیام یک ردیف، با ``session_id``/``project_id``/
    ``user_id`` روی خودش). بنابراین باز کردن دوباره‌ی یک گفتگوی قدیمی، تاریخچه‌ی
    واقعی پیام‌هایش را نشان می‌دهد.

    ``id`` عمداً یک UUID رشته‌ای است (نه عدد خودکارافزاینده) -- دقیقاً مثل
    ``KnowledgeBase.id``. دلیل، همان تضمین «یکتایی/نسخه‌بندی» این پروژه است:
    شناسه‌ی یک گفتگو باید در کل سیستم یکتا باشد تا کلید ترکیبی
    ``(project_id, session_id, assistant_message_id)`` که job پس‌زمینه‌ی هر پرسش
    را مشخص می‌کند، تحت هیچ شرایطی (حتی یک شناسه‌ی تکراری بین دو پروژه) دو کار
    متفاوت را به هم نچسباند. ``chat_messages.id`` هم همین‌طور UUID است.

    جایگاه امنیتی: مثل ``KnowledgeBase``، یک گفتگو متعلق به یک ``user_id`` **و**
    یک ``project_id`` مشخص است؛ هیچ گفتگویی با شناسه‌اش از پروژه/کاربر دیگر
    خوانده نمی‌شود (همان الگوی «۴۰۴ نه ۴۰۳» در ``api/repositories/chat_sessions.py``).

    ``updated_at`` هنگام هر پرسشِ موفق در همان گفتگو تازه می‌شود، بنابراین فهرست
    گفتگوها (جدیدترین اول) با «آخرین استفاده» مرتب می‌شود، نه فقط با زمان ساخت.
    """

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
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
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class ChatMessage(Base):
    """یک پیام از یک گفتگوی چت‌بات مالی (پرسش کاربر یا پاسخ دستیار).

    **سه‌گانه‌ی ``session_id``/``project_id``/``user_id`` روی خودِ ردیف است** (نه
    فقط قابل‌استخراج با join به ``chat_sessions``). این یک تصمیم عمدی است، دقیقاً
    به همان دلیلی که ``knowledge_bases`` ستون ``user_id`` خودش را دارد: هر پرس‌وجو
    و هر بررسی جداسازی (``list_by_session``، endpoint polling پیام، ساخت پیام) یک
    پرس‌وجوی *تک‌جدولی و سه‌دامنه‌ای* می‌شود، پس یک باگ در یک join نمی‌تواند پیام
    یک کاربر/پروژه/گفتگو را به دیگری نشت دهد. ایندکس ترکیبی ``ix_chat_messages_scope``
    (به‌همراه ایندکس ``session_id``) همان چیزی است که این پرس‌وجوها را سریع نگه
    می‌دارد -- و عمداً همان الگوی «خودکفا»ی جدول‌های پایگاه‌دانش را تکرار می‌کند.

    چرخه‌ی حیات ``status``:

    * ``complete`` -- پیام کاربر، و همچنین پاسخی که با موفقیت تولید شده است.
    * ``pending``  -- ردیف جانشینِ پاسخ دستیار که *پیش از* شروع فراخوانی مدل
      ساخته می‌شود تا UI بتواند همان لحظه شروع به polling کند.
    * ``failed``   -- تولید پاسخ شکست خورده است؛ دلیلش در ``error_message`` است.

    ``confidence``/``sources``/``route_reasoning`` فقط برای پیام دستیار معنا دارند
    (``sources`` یک متن JSON است، نه یک ستون JSON -- شکل سبک همین جدول).
    """

    __tablename__ = "chat_messages"
    __table_args__ = (
        # پرس‌وجوی داغ «پیام‌های همین گفتگو برای همین کاربر و همین پروژه» --
        # یک ایندکس سه‌ستونی، نه سه ایندکس جدا.
        Index("ix_chat_messages_scope", "session_id", "project_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="complete", index=True)
    confidence: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    sources: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    route_reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[ChatSession] = relationship(back_populates="messages")
