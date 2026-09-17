"""رجیستری کارگاه‌ها -- ستون فقرات توسعه‌پذیری این فاز.

«چه کارگاه‌هایی وجود دارند» داده است، نه چیزی که در چند قالب/روتر جداگانه
هاردکد شود. هر کارگاه یک ``WorkshopDefinition`` ثبت می‌کند و بقیه‌ی کد (صفحه‌ی
پروژه برای کشیدن کارت‌ها، ناوبری، ثبت روتر در FastAPI، صفحه‌ی خود کارگاه، ثبت
تاریخچه) فقط از طریق همین رجیستری با کارگاه‌ها کار می‌کند.

نتیجه‌ی عملی: افزودن کارگاه چهارم (مثلاً «تحلیل بودجه» در فاز بعد) فقط یک ماژول
جدید + یک ردیف ثبت در ``api/workshops/__init__.py`` است؛ هیچ قالبی تغییر نمی‌کند و
هیچ لیستی از نام کارگاه‌ها جای دیگری تکرار نمی‌شود.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from fastapi import APIRouter, Request
from fastapi.responses import Response

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@dataclass(frozen=True)
class ResultArtifact:
    """خروجی نهایی یک اجرای موفق، آماده‌ی ذخیره در تاریخچه.

    ``summary`` فقط متادیتای کوچک نتیجه است (درصد تطابق، تعداد موارد، نام فایل
    مبدأ و...) -- نه محتوای فایل ورودی و نه مسیر آن.
    """

    summary: dict[str, Any]
    filename: Optional[str] = None
    content: Optional[bytes] = None
    mime_type: str = DOCX_MIME


@dataclass(frozen=True)
class WorkshopDefinition:
    """توصیف کامل یک کارگاه -- تنها منبع حقیقت درباره‌ی آن."""

    slug: str
    display_name_fa: str
    description_fa: str
    icon: str
    accent: str  # نام رنگ در CSS: teal | navy | gold | emerald
    router: APIRouter
    # قالب صفحه‌ی این کارگاه (داخل یک پروژه)
    page_template: str
    # رندر صفحه‌ی خود کارگاه (داخل یک پروژه). هر کارگاه داده‌ی متفاوتی برای
    # قالبش لازم دارد، به همین دلیل یک callable است و نه فقط نام قالب.
    page_renderer: Callable[[Request, Any, Any], Response]
    # تخمین درصد پیشرفت از روی دیکشنری خام job (منطق مخصوص هر کارگاه)
    estimate_progress: Callable[[dict[str, Any]], int]
    # تبدیل job تمام‌شده به artifact قابل ذخیره در تاریخچه
    collect_result: Callable[[dict[str, Any]], ResultArtifact]
    # آیا خروجی این job همین حالا قابل دانلود است؟ (برای پنل کارهای در جریان)
    has_download: Callable[[dict[str, Any]], bool]
    # چند برچسب کوتاه فارسی از متادیتای نتیجه‌ی یک اجرای تمام‌شده، برای نمایش در
    # فهرست تاریخچه. هر کارگاه خودش می‌داند کدام اعدادش مهم‌اند، بنابراین قالب
    # تاریخچه کاملاً عمومی می‌ماند.
    summary_chips_fa: Callable[[dict[str, Any]], list[str]]
    badge_fa: Optional[str] = None
    highlights_fa: tuple[str, ...] = field(default_factory=tuple)
    # کلیدهای تنظیماتی که این کارگاه مصرف می‌کند (فاز تنظیمات از این استفاده می‌کند)
    settings_keys: tuple[str, ...] = ()
    # آیا تنظیمات اختصاصی هوش مصنوعی *واقعاً* هنگام اجرا اعمال می‌شود؟ فرم تنظیمات
    # فقط برای کارگاه‌هایی نمایش داده می‌شود که این پرچم را True کرده‌اند -- تا هیچ
    # فرمی به کاربر وعده‌ی اثری ندهد که در عمل وجود ندارد.
    settings_applied: bool = False
    # تست اتصال این کارگاه: یک ``AISettings`` حل‌شده می‌گیرد و در صورت موفقیت پیام
    # فارسی برمی‌گرداند (در غیر این صورت استثنا می‌اندازد). None یعنی این کارگاه
    # دکمه‌ی «تست اتصال» ندارد.
    test_connection: Optional[Callable[[Any], str]] = None

    # ------------------------------------------------------------------
    # آدرس‌ها -- یک‌جا محاسبه می‌شوند تا هیچ قالبی مسیر را دستی نسازد
    # ------------------------------------------------------------------
    @property
    def api_prefix(self) -> str:
        """پیشوند endpoint های JSON این کارگاه داخل یک پروژه."""
        return f"/api/projects/{{project_id}}/{self.slug}"

    def api_url(self, project_id: int) -> str:
        return f"/api/projects/{project_id}/{self.slug}"

    def page_url(self, project_id: int) -> str:
        return f"/projects/{project_id}/{self.slug}"

    def run_download_url(self, project_id: int, run_id: int) -> str:
        return f"{self.api_url(project_id)}/runs/{run_id}/download"

    def job_url(self, project_id: int, job_id: str) -> str:
        return f"{self.api_url(project_id)}/jobs/{job_id}"


WORKSHOPS: dict[str, WorkshopDefinition] = {}


def register(definition: WorkshopDefinition) -> WorkshopDefinition:
    """ثبت یک کارگاه در رجیستری (در زمان ایمپورت ماژول همان کارگاه)."""
    if definition.slug in WORKSHOPS:
        raise ValueError(f"workshop slug already registered: {definition.slug}")
    WORKSHOPS[definition.slug] = definition
    return definition


def get(slug: str) -> Optional[WorkshopDefinition]:
    return WORKSHOPS.get(slug)


def all_workshops() -> list[WorkshopDefinition]:
    """همه‌ی کارگاه‌ها به ترتیب ثبت (ترتیب کارت‌ها در صفحه‌ی پروژه)."""
    return list(WORKSHOPS.values())


def workshops_with_settings() -> list[WorkshopDefinition]:
    """کارگاه‌هایی که تنظیمات اختصاصی هوش مصنوعی‌شان واقعاً اعمال می‌شود.

    پنل «تنظیمات» صفحه‌ی پروژه و endpoint های تنظیمات هر دو از همین لیست
    می‌آیند، بنابراین افزودن یک کارگاه جدیدِ متصل به تنظیمات هیچ تغییری در
    قالب‌ها یا روتر لازم ندارد.
    """
    return [workshop for workshop in WORKSHOPS.values() if workshop.settings_applied]
