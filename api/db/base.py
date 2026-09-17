"""لایه‌ی پایگاه‌داده‌ی داشبورد (SQLAlchemy روی SQLite).

این پایگاه‌داده **جدا از** PostgreSQL مربوط به ``db_management/`` است (که یک
سیستم قدیمی و مستقل برای ذخیره‌سازی دسته‌ای داده‌های بودجه است و طبق محدوده‌ی
این فاز دست‌نخورده باقی می‌ماند). این‌جا فقط چیزهایی ذخیره می‌شوند که خودِ
داشبورد لازم دارد: کاربران، پروژه‌ها، تاریخچه‌ی اجرای کارگاه‌ها و تنظیمات هر
کارگاه در هر پروژه -- و هرگز محتوای فایل‌های ورودی کاربر.

SQLite انتخاب شده تا اجرا/دموی محلی به هیچ سرویس بیرونی نیاز نداشته باشد؛ همه‌ی
دسترسی‌های داده از لایه‌ی ``api/repositories/`` عبور می‌کنند تا تغییر به
Postgres در آینده تغییری محدود و متمرکز باشد.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from api.config import get_settings


class Base(DeclarativeBase):
    """کلاس پایه‌ی همه‌ی مدل‌های ORM داشبورد."""


def _build_engine() -> Engine:
    settings = get_settings()
    url = settings.database_url

    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        # اتصال SQLite بین تردها به اشتراک گذاشته می‌شود (ترد پس‌زمینه‌ی هر job
        # هنگام پایان کار، ردیف تاریخچه را می‌نویسد).
        connect_args["check_same_thread"] = False
        # مسیر فایل پایگاه‌داده را از پیش می‌سازیم تا SQLAlchemy در اولین اتصال
        # خطای «unable to open database file» ندهد.
        db_path = url.split("///", 1)[-1]
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, connect_args=connect_args, future=True)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _record) -> None:  # pragma: no cover - وابسته به درایور
            # ``foreign_keys`` در SQLite به‌صورت پیش‌فرض خاموش است، بنابراین
            # ON DELETE CASCADE تعریف‌شده در مدل‌ها بدون این تنظیم اجرا نمی‌شود.
            # WAL هم اجازه می‌دهد خواندن‌ها هنگام نوشتن ترد پس‌زمینه بلاک نشوند.
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """ساخت جدول‌های غایب (idempotent). جایگزین سبک Alembic برای این MVP است."""
    # ماژول مدل‌ها باید پیش از create_all ایمپورت شود تا متادیتا پر باشد.
    from api.db import models  # noqa: F401

    Base.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """وابستگی FastAPI: یک نشست برای هر درخواست."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
