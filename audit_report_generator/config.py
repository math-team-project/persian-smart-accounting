"""
تنظیمات اتصال به LLM.

این پکیج از کتابخانه رسمی ``openai`` استفاده می‌کند، اما چون تقریباً تمام
ارائه‌دهندگان (OpenAI، Azure OpenAI، OpenRouter، Together، سرورهای محلی
سازگار با OpenAI مثل vLLM/Ollama و ...) از همین ساختار API پیروی می‌کنند،
با تغییر ``base_url`` و ``api_key`` می‌توانید هر provider دلخواه را ست کنید.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from .exceptions import InputValidationError


@dataclass
class LLMConfig:
    """پیکربندی اتصال به مدل زبانی.

    Parameters
    ----------
    api_key:
        کلید API. اگر ست نشود از متغیر محیطی ``AUDIT_LLM_API_KEY`` (و در نبود آن
        ``OPENAI_API_KEY``) خوانده می‌شود.
    base_url:
        آدرس پایه provider. برای OpenAI معمولاً نیازی به ست کردن نیست.
        برای provider های دیگر مثلاً:
        ``https://openrouter.ai/api/v1`` یا آدرس سرور داخلی خودتان.
    model:
        نام مدل (مثلاً ``gpt-4o-mini``, ``gpt-4.1``, یا نام مدل provider دیگر).
    temperature:
        درجه خلاقیت مدل؛ برای گزارش رسمی مقدار پایین (0 تا 0.4) توصیه می‌شود.
    max_output_tokens:
        سقف توکن خروجی هر فراخوانی.
    request_timeout:
        سقف زمان (ثانیه) برای هر فراخوانی API.
    max_retries:
        تعداد تلاش مجدد در صورت بروز خطای موقت (rate limit، timeout، خطای شبکه).
    retry_backoff_seconds:
        زمان اولیه انتظار بین تلاش‌ها (به‌صورت نمایی افزایش می‌یابد).
    extra_client_kwargs:
        هر آرگومان اضافی که provider خاص شما نیاز دارد و باید مستقیماً به
        سازنده ``openai.OpenAI`` پاس داده شود (مثلاً ``organization`` یا
        ``default_headers``).
    """

    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: str = "gpt-4o-mini"
    temperature: float = 0.2
    max_output_tokens: int = 20000
    request_timeout: float = 450.0
    max_retries: int = 3
    retry_backoff_seconds: float = 2.0
    extra_client_kwargs: dict = field(default_factory=dict)

    def resolve_api_key(self) -> str:
        key = self.api_key or os.environ.get("AUDIT_LLM_API_KEY") or os.environ.get(
            "OPENAI_API_KEY"
        )
        if not key:
            raise InputValidationError(
                "کلید API تنظیم نشده است. یا آن را هنگام ساخت LLMConfig(api_key=...) "
                "بدهید یا متغیر محیطی AUDIT_LLM_API_KEY / OPENAI_API_KEY را ست کنید."
            )
        return key
