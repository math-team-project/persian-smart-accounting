"""رجیستری کارگاه‌ها (منبع واحد «چه کارگاه‌هایی وجود دارند»).

ایمپورت کردن این پکیج، ماژول هر کارگاه را ایمپورت می‌کند و هرکدام خودشان را در
رجیستری ثبت می‌کنند. افزودن کارگاه جدید (مثلاً «تحلیل بودجه» در فاز بعد) یعنی:

    ۱) یک ماژول جدید در همین پکیج با ``router``، ``render_page`` و فراخوانی
       ``register(WorkshopDefinition(...))``،
    ۲) یک ردیف ``from api.workshops import <module>`` در همین فایل.

هیچ قالب HTML، هیچ ناوبری و هیچ لیست دیگری تغییر نمی‌کند -- صفحه‌ی پروژه کارت‌ها
را از ``WORKSHOPS`` می‌سازد و ناوبری هم همین رجیستری را می‌خواند.
"""
from __future__ import annotations

from api.workshops.registry import (
    WORKSHOPS,
    ResultArtifact,
    WorkshopDefinition,
    all_workshops,
    get,
    register,
    workshops_with_settings,
)

# ایمپورت‌ها *بعد* از ایمپورت registry هستند و عمداً ترتیب دارند: هر ماژول در زمان
# ایمپورت، تعریف خودش را ثبت می‌کند. ترتیب ایمپورت = ترتیب نمایش (کارت‌های صفحهٔ
# پروژه و پیوندهای ناوبری)، به همین دلیل چک‌لیست -- کارگاه اصلی -- اول می‌آید.
from api.workshops import checklist, audit_summary, budget_analysis  # noqa: E402,F401  (ثبت در رجیستری)

__all__ = [
    "WORKSHOPS",
    "ResultArtifact",
    "WorkshopDefinition",
    "all_workshops",
    "audit_summary",
    "budget_analysis",
    "checklist",
    "get",
    "register",
    "workshops_with_settings",
]
