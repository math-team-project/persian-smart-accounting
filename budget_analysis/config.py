"""پیکربندی آستانه‌ها و پارامترهای سیاستی موتور تحلیل بودجه.

مقادیر پیش‌فرض دقیقاً همان‌هایی هستند که در بخش «آستانه‌ها و پارامترهای پیش‌فرض»
پرامپت مرجع آمده‌اند. دو قاعده‌ی مهم این‌جا رعایت می‌شود:

۱) سال‌ها هیچ‌گاه در کد hard-code نمی‌شوند -- ``base_year``/``current_year`` از
   ورودی کاربر یا از خود اسناد تشخیص داده می‌شوند و در ``BudgetConfig``
   *خالی* می‌مانند مگر اینکه کسی صریحاً مقدار بدهد.

۲) این آستانه‌ها «پارامتر سیاستی/مدیریتی سامانه» هستند، نه حکم قانونی؛ این
   تفکیک در متن پرامپت لایه‌ی تحلیل به مدل هم اعلام می‌شود تا آن‌ها را به‌عنوان
   «الزام قانونی» معرفی نکند.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Mapping, Optional

__all__ = ["BudgetConfig", "DEFAULT_CONFIG"]


@dataclass(frozen=True)
class BudgetConfig:
    """آستانه‌های موتور پایش (همه نسبتی هستند مگر خلاف آن ذکر شود)."""

    # --- سال‌ها: خالی یعنی «از ورودی کاربر یا از خود اسناد تشخیص بده» ---
    base_year: Optional[str] = None
    current_year: Optional[str] = None

    # --- محور ۳: رشد حقوق، مزایا و اقلام مرتبط ---
    salary_base_growth_rate: float = 0.35
    salary_tolerance: float = 0.05
    salary_lower_bound: float = 0.30
    salary_upper_bound: float = 0.40

    # --- محور ۴: ساختار هزینه‌های نیروی انسانی ---
    personnel_cost_cap: float = 0.30
    personnel_tolerance: float = 0.01
    total_workforce_cap: float = 0.40

    # --- محور ۲: حمایت و توسعه فناوری ---
    direct_tech_units_min_share: float = 0.30
    total_tech_min_share: float = 0.40
    public_funding_min_ratio: float = 0.50

    # --- محور ۵: مانده و انتقال اعتبارات سنواتی ---
    unused_budget_max_ratio: float = 0.30

    # --- تطبیق بین‌فرمی ---
    cross_form_default_tolerance: float = 0.05
    form5_1_form10_tolerance: float = 0.05

    # --- محور ۶: ثبات نیروی انسانی (واحد: نفر) ---
    workforce_count_change_tolerance: int = 0

    # --- گزارش‌دهی ---
    max_top_findings: int = 10
    max_detail_findings_per_axis: int = 10

    # --- تنظیمات دلخواه آینده (فاز تنظیمات می‌تواند این‌جا تزریق کند) ---
    extras: Mapping[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    @classmethod
    def from_mapping(cls, data: Optional[Mapping[str, Any]]) -> "BudgetConfig":
        """ساخت پیکربندی از یک نگاشت (ورودی کاربر یا تنظیمات پروژه).

        کلیدهای ناشناخته نادیده گرفته نمی‌شوند بلکه در ``extras`` نگه داشته
        می‌شوند تا معیارهای توسعه‌یافته‌ی آینده بتوانند بدون تغییر این فایل
        پارامتر خودشان را بگذارند (محورها قابل توسعه‌اند و به شش محور محدود
        نیستند).
        """
        if not data:
            return DEFAULT_CONFIG
        known = {f for f in cls.__dataclass_fields__ if f != "extras"}
        kwargs: dict[str, Any] = {}
        extras: dict[str, Any] = {}
        for key, value in data.items():
            if key in known and value is not None:
                kwargs[key] = value
            elif key != "extras":
                extras[key] = value
        config = replace(DEFAULT_CONFIG, **kwargs)
        if extras:
            merged = {**dict(config.extras), **extras}
            config = replace(config, extras=merged)
        return config

    def to_prompt_dict(self) -> dict[str, Any]:
        """نگاشت قابل‌درج در پرامپت (بدون کلیدهای None تا پرامپت تمیز بماند)."""
        data = asdict(self)
        data.pop("extras", None)
        return {key: value for key, value in data.items() if value is not None}


DEFAULT_CONFIG = BudgetConfig()
