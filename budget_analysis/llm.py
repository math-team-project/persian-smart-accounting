"""لایه‌ی ارتباط با مدل زبانی در کارگاه تحلیل بودجه (مرحله‌ی ۲).

این ماژول تنها جایی است که «مدل زبانی» صدا زده می‌شود، و عمداً پشت یک کلاس کوچک
(``BudgetAnalysisLLM`` + ``BudgetLLMSettings``) پنهان شده است تا فاز تنظیمات
بتواند بدون بازنویسی، کلید/آدرس/مدل هر پروژه یا هر کارگاه را تزریق کند.

پیاده‌سازی از زیرساخت موجود پروژه بازاستفاده می‌کند:
``audit_report_generator.llm_client.LLMClient`` (فراخوانی سازگار با OpenAI SDK +
retry نمایی + استخراج بلوک JSON از خروجی مدل) و ``audit_report_generator.config.LLMConfig``.
هیچ منطق جدیدی برای تماس شبکه‌ای/retry نوشته نشده است.

ترتیب انتخاب پیش‌فرض کلید و آدرس (همان متغیرهای محیطی مستندشده‌ی پروژه):
``AUDIT_REPORT_LLM_API_KEY`` ← ``API_KEY_OPENROUTER`` ← ``B_AI_API_KEY``، و
``AUDIT_REPORT_LLM_BASE_URL`` ← ``AUDIT_REPORT_LLM_MODEL``.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, replace
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)

__all__ = [
    "BudgetAnalysisLLM",
    "BudgetLLMError",
    "BudgetLLMSettings",
]

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"

# طول خروجی موردنیاز این کارگاه: ماتریس کامل معیارها + یافته‌ها معمولاً چند ده
# هزار نویسه است. نکته‌ی مهمی که نسخه‌ی قبلی این مقدار از آن غافل بود: برای
# مدل‌های «استدلالی» (reasoning models) توکن‌های استدلال هم داخل همین سقف
# ``max_tokens`` حساب می‌شوند. در یک اجرای واقعی با اسناد نمونه‌ی ۱۴۰۴/۱۴۰۵،
# حدود ۱۷ هزار توکن صرف استدلال شد و با سقف ۲۰٫۰۰۰ توکن، JSON خروجی در میانه‌ی
# کار بریده شد (``finish_reason=length``) و اجرا با خطا متوقف می‌شد. سقف زیر
# با احتساب استدلال + متن JSON تنظیم شده و از سقف خروجی مدل پیش‌فرض
# (۶۵٫۵۳۶ توکن) پایین‌تر است. فاز تنظیمات می‌تواند آن را برای هر پروژه تغییر دهد.
DEFAULT_MAX_OUTPUT_TOKENS = 48_000


class BudgetLLMError(Exception):
    """خطای فراخوانی مدل زبانی -- پیام آن فارسی و قابل‌نمایش به کاربر است."""


def _env_api_key() -> Optional[str]:
    """کلید پیش‌فرض سامانه: همان متغیرهای محیطی مستندشده در README."""
    return (
        os.environ.get("AUDIT_REPORT_LLM_API_KEY")
        or os.environ.get("API_KEY_OPENROUTER")
        or os.environ.get("B_AI_API_KEY")
    )


@dataclass
class BudgetLLMSettings:
    """تنظیمات اتصال به مدل -- نقطه‌ی تزریق تنظیمات فاز بعد."""

    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    temperature: float = 0.2
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    request_timeout: float = 450.0
    max_retries: int = 3
    retry_backoff_seconds: float = 2.0

    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls) -> "BudgetLLMSettings":
        """پیش‌فرض‌های سامانه از متغیرهای محیطی (بدون هیچ کلید hard-code)."""
        return cls(
            api_key=_env_api_key(),
            base_url=os.environ.get("AUDIT_REPORT_LLM_BASE_URL") or DEFAULT_BASE_URL,
            model=os.environ.get("AUDIT_REPORT_LLM_MODEL") or DEFAULT_MODEL,
        )

    def resolved(self) -> "BudgetLLMSettings":
        """نسخه‌ای که همه‌ی فیلدهای لازم آن مقدار دارند."""
        return replace(
            self,
            api_key=self.api_key or _env_api_key(),
            base_url=self.base_url or DEFAULT_BASE_URL,
            model=self.model or DEFAULT_MODEL,
        )

    def with_overrides(self, **overrides: Any) -> "BudgetLLMSettings":
        """تزریق تنظیمات پروژه/کارگاه (فاز تنظیمات) با نادیده‌گرفتن مقادیر خالی."""
        clean = {key: value for key, value in overrides.items() if value}
        return replace(self, **clean) if clean else self

    @property
    def masked_key(self) -> str:
        key = self.api_key or ""
        return f"{key[:5]}…" if key else "(تنظیم نشده)"


class BudgetAnalysisLLM:
    """پوشش نازک روی کلاینت موجود پروژه، با خروجی JSON و خطای فارسی یکدست."""

    def __init__(self, settings: Optional[BudgetLLMSettings] = None) -> None:
        self.settings = (settings or BudgetLLMSettings()).resolved()

    def complete_json(self, messages: Sequence[dict[str, str]]) -> Any:
        """پیام‌ها را می‌فرستد و خروجی را به‌صورت JSON پارس‌شده برمی‌گرداند.

        Raises:
            BudgetLLMError: در نبود کلید، خطای سرویس، یا خروجی غیرقابل‌پارس.
        """
        if not self.settings.api_key:
            raise BudgetLLMError(
                "کلید سرویس هوش مصنوعی تنظیم نشده است. متغیر محیطی "
                "AUDIT_REPORT_LLM_API_KEY (یا API_KEY_OPENROUTER) را تنظیم کنید."
            )

        from audit_report_generator.config import LLMConfig
        from audit_report_generator.exceptions import LLMGenerationError
        from audit_report_generator.llm_client import LLMClient

        config = LLMConfig(
            api_key=self.settings.api_key,
            base_url=self.settings.base_url,
            model=self.settings.model,
            temperature=self.settings.temperature,
            max_output_tokens=self.settings.max_output_tokens,
            request_timeout=self.settings.request_timeout,
            max_retries=self.settings.max_retries,
            retry_backoff_seconds=self.settings.retry_backoff_seconds,
        )
        logger.info(
            "budget analysis LLM call (model=%s, base_url=%s, key=%s)",
            config.model,
            config.base_url,
            self.settings.masked_key,
        )
        try:
            client = LLMClient(config)
            return client.chat_json(list(messages))
        except LLMGenerationError as exc:
            raise BudgetLLMError(
                f"تحلیل هوشمند بودجه ناموفق بود: {exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 -- هر خطای سرویس یک پیام فارسی می‌گیرد
            raise BudgetLLMError(
                f"خطای غیرمنتظره در ارتباط با سرویس هوش مصنوعی: {exc}"
            ) from exc
