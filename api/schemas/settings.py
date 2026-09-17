"""مدل‌های Pydantic تنظیمات هوش مصنوعی هر کارگاه در هر پروژه.

این مدل‌ها فقط «ورودی/خروجی» فرم تنظیمات‌اند؛ خودِ کلید API هرگز در هیچ پاسخ
JSON برنمی‌گردد -- تنها ``api_key_set`` که نشان می‌دهد مقداری ذخیره شده است.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class WorkshopAISettingsInput(BaseModel):
    """مقادیر فرم تنظیمات یک کارگاه.

    همه‌ی فیلدها اختیاری‌اند: خالی‌بودن یعنی «از پیش‌فرض سامانه استفاده کن».
    ``api_key`` استثناست -- چون مقدار ذخیره‌شده هرگز به فرم برنمی‌گردد،
    خالی‌بودن آن یعنی «همان کلید قبلی را نگه دار»؛ پاک‌کردن کلید فقط با
    ``clear_api_key=true`` انجام می‌شود.
    """

    api_key: Optional[str] = None
    clear_api_key: bool = False
    api_url: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_output_tokens: Optional[int] = None


class WorkshopAISettingsOut(BaseModel):
    """وضعیت ذخیره‌شده‌ی تنظیمات یک کارگاه -- بدون هیچ رازی."""

    slug: str
    display_name_fa: str
    api_key_set: bool
    api_url: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_output_tokens: Optional[int] = None


class AIDefaultsOut(BaseModel):
    """پیش‌فرض‌های سامانه (از محیط/``.env``) برای نمایش در راهنمای فرم."""

    api_url: str
    model: str
    temperature: float
    max_output_tokens: int
    api_key_set: bool


class ProjectAISettingsOut(BaseModel):
    project_id: int
    defaults: AIDefaultsOut
    workshops: list[WorkshopAISettingsOut]


class ConnectionTestOut(BaseModel):
    """نتیجه‌ی «تست اتصال» پیش از ذخیره‌ی تنظیمات."""

    ok: bool
    message: str
