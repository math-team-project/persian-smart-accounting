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

import logging
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from api.config import get_settings

logger = logging.getLogger(__name__)


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

    _upgrade_legacy_chat_sessions()
    Base.metadata.create_all(engine)


def _upgrade_legacy_chat_sessions() -> None:
    """جدول ``chat_sessions`` نسخه‌ی قدیمی (شناسه‌ی عددی) را کنار می‌گذارد.

    پیش از این فاز، ``chat_sessions.id`` یک عدد خودکارافزاینده بود و هیچ محتوای
    گفتگویی ذخیره نمی‌شد. از این فاز به بعد این شناسه یک UUID رشته‌ای است (تا کلید
    ترکیبی ``(project_id, session_id, assistant_message_id)`` واقعاً جهانی‌یکتا
    باشد). ``create_all`` نمی‌تواند نوع یک ستون موجود را عوض کند، و یک ستون
    ``INTEGER PRIMARY KEY`` در SQLite حتی یک رشته‌ی UUID را هم نمی‌پذیرد
    («datatype mismatch») -- یعنی بدون این ارتقای کوچک، اولین تلاش برای ساخت
    گفتگو در یک پایگاه‌داده‌ی موجود شکست می‌خورد.

    طبق طراحی صریح همان نسخه‌ی قدیمی، آن جدول *فقط* عنوان/زمان دو گفتگو را داشت و
    هیچ پیامی نداشت؛ پس کنارگذاشتنش هیچ محتوایی را از دست نمی‌دهد. این تابع فقط
    روی SQLite و فقط وقتی اجرا می‌شود که واقعاً شکل قدیمی تشخیص داده شود (ستون
    ``id`` عددی باشد)، بنابراین روی پایگاه‌داده‌ی درست هیچ اثری ندارد.
    """
    if not engine.url.drivername.startswith("sqlite"):  # pragma: no cover - SQLite پیش‌فرض است
        return
    inspector = inspect(engine)
    if not inspector.has_table("chat_sessions"):
        return
    columns = {column["name"]: column for column in inspector.get_columns("chat_sessions")}
    id_column = columns.get("id")
    if id_column is None or "INT" not in str(id_column["type"]).upper():
        return

    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE chat_sessions")
    logger.warning(
        "legacy chat_sessions table (integer id) dropped; it stored titles only, "
        "and the new schema uses a UUID primary key"
    )


def get_session() -> Iterator[Session]:
    """وابستگی FastAPI: یک نشست برای هر درخواست."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
