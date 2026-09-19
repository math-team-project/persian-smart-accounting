"""لایه‌ی ارتباط با LLM از طریق کتابخانه‌ی ``openai``.

این ماژول عمداً مستقیماً به OpenAI وابسته نیست؛ با ست کردن ``base_url`` در
``LLMConfig`` می‌توان هر provider سازگار با OpenAI Chat Completions API را
استفاده کرد (Azure OpenAI، OpenRouter، Together، vLLM محلی و...).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List

from .config import LLMConfig
from .exceptions import LLMGenerationError

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
    from openai import (
        APIConnectionError,
        APIError,
        APIStatusError,
        APITimeoutError,
        RateLimitError,
    )
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "کتابخانه 'openai' نصب نیست. با دستور `pip install openai` آن را نصب کنید."
    ) from exc

# خطاهایی که ارزش تلاش مجدد دارند (موقتی/شبکه‌ای هستند)
_RETRYABLE_EXCEPTIONS = (APIConnectionError, APITimeoutError, RateLimitError)


def _strip_code_fence(text: str) -> str:
    """اگر مدل خروجی را داخل ```json ... ``` بسته باشد، آن را باز می‌کند."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _extract_json_block(text: str) -> str:
    """در صورتی که مدل متن اضافه قبل/بعد از JSON نوشته باشد، فقط بخش JSON (چه
    آرایه `[...]` و چه شیء `{...}`) را استخراج می‌کند."""
    first_obj = text.find("{")
    first_arr = text.find("[")
    candidates = [i for i in (first_obj, first_arr) if i != -1]
    if not candidates:
        raise LLMGenerationError(
            "خروجی مدل حاوی یک شیء/آرایه JSON قابل‌تشخیص نبود.\n"
            f"خروجی خام (کوتاه‌شده): {text[:500]!r}"
        )
    start = min(candidates)
    opening = text[start]
    closing = "}" if opening == "{" else "]"
    end = text.rfind(closing)
    if end == -1 or end < start:
        raise LLMGenerationError(
            "خروجی مدل حاوی یک شیء/آرایه JSON کامل نبود (بسته نشده).\n"
            f"خروجی خام (کوتاه‌شده): {text[:500]!r}"
        )
    return text[start : end + 1]


def _usage_note(response: Any) -> str:
    """توضیح خوانا از مصرف توکن پاسخ (برای پیام خطا).

    تعداد توکن‌های «استدلال» (``reasoning``) در همین مصرف و در سقف ``max_tokens``
    حساب می‌شود؛ بدون نشان دادن آن، کاربر نمی‌فهمد چرا با وجود سقف بزرگ، خروجی
    بریده شده است.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return ""
    parts: list[str] = []
    completion = getattr(usage, "completion_tokens", None)
    if completion is not None:
        parts.append(f"توکن خروجی: {completion:,}")
    details = getattr(usage, "completion_tokens_details", None)
    reasoning = getattr(details, "reasoning_tokens", None) if details is not None else None
    if reasoning:
        parts.append(f"که {reasoning:,} توکن آن صرف استدلال مدل شده است")
    prompt_tokens = getattr(usage, "prompt_tokens", None)
    if prompt_tokens is not None:
        parts.append(f"توکن ورودی: {prompt_tokens:,}")
    return "، ".join(parts)


class LLMClient:
    """کلاینت نازک روی openai.OpenAI با retry و مدیریت خطای یکپارچه."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self._client = OpenAI(
            api_key=config.resolve_api_key(),
            base_url=config.base_url,
            timeout=config.request_timeout,
            **(config.extra_client_kwargs or {}),
        )

    def _raise_if_incomplete(self, response: Any, content: str) -> None:
        """خروجی بریده/خالی را فوراً و با پیام قابل‌اقدام گزارش می‌کند (بدون تلاش مجدد).

        ``finish_reason == "length"`` یعنی پاسخ *بریده* شده است. این حالت با
        تکرار همان درخواست درست نمی‌شود (همان سقف، همان برش) و تنها اثرش چند
        دقیقه انتظار اضافی و سپس خطای مبهم JSON است؛ بنابراین اینجا صریح گزارش
        می‌شود. حالت «متن خالی با مصرف کامل توکن» هم تقریباً همیشه یعنی تمام سقف
        صرف استدلال مدل شده است و همان راه‌حل را دارد.
        """
        finish_reason = getattr(response.choices[0], "finish_reason", None)
        note = _usage_note(response)
        suffix = f" ({note})" if note else ""
        if finish_reason == "length":
            raise LLMGenerationError(
                "خروجی مدل به سقف مجاز توکن رسید و پیش از کامل شدن بریده شد "
                f"(finish_reason=length). سقف فعلی: {self.config.max_output_tokens:,} توکن{suffix}. "
                "این سقف شامل توکن‌های استدلال مدل هم می‌شود، پس برای مدل‌های استدلالی "
                "باید مقدار بزرگ‌تری بگیرد. مقدار «حداکثر طول خروجی» را در تنظیمات هوش "
                "مصنوعی این کارگاه افزایش دهید و اجرا را دوباره شروع کنید."
            )
        if not content.strip():
            raise LLMGenerationError(
                "مدل هیچ متنی برنگرداند (احتمالاً همه‌ی سقف توکن صرف استدلال شده است)"
                f"{suffix}. مقدار «حداکثر طول خروجی» را افزایش دهید و دوباره تلاش کنید."
            )

    def chat_json(self, messages: List[Dict[str, str]]) -> Any:
        """یک فراخوانی chat completion انجام می‌دهد و خروجی را به‌صورت JSON پارس می‌کند.

        در صورت خطای موقتی (شبکه/timeout/rate-limit) تا ``max_retries`` بار با
        backoff نمایی دوباره تلاش می‌کند. در صورت شکست نهایی یا خروجی غیرقابل‌پارس،
        ``LLMGenerationError`` پرتاب می‌شود.
        """
        last_error: Exception | None = None
        for attempt in range(1, self.config.max_retries + 1):
            try:
                response = self._client.chat.completions.create(
                    model=self.config.model,
                    messages=messages,
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_output_tokens,
                )
                content = response.choices[0].message.content or ""
                self._raise_if_incomplete(response, content)

                cleaned = _strip_code_fence(content)
                try:
                    return json.loads(cleaned)
                except json.JSONDecodeError:
                    cleaned = _extract_json_block(cleaned)
                    return json.loads(cleaned)

            except _RETRYABLE_EXCEPTIONS as exc:
                last_error = exc
                wait = self.config.retry_backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "خطای موقتی در فراخوانی LLM (تلاش %s/%s): %s — %.1f ثانیه صبر می‌شود.",
                    attempt,
                    self.config.max_retries,
                    exc,
                    wait,
                )
                if attempt < self.config.max_retries:
                    time.sleep(wait)
                continue

            except (APIStatusError, APIError) as exc:
                # خطاهای غیرموقتی (مثلاً کلید نامعتبر، مدل ناموجود، ورودی رد شده)
                raise LLMGenerationError(f"خطای API هنگام فراخوانی مدل: {exc}") from exc

            except json.JSONDecodeError as exc:
                last_error = exc
                logger.warning(
                    "خروجی مدل JSON معتبر نبود (تلاش %s/%s): %s",
                    attempt,
                    self.config.max_retries,
                    exc,
                )
                if attempt < self.config.max_retries:
                    time.sleep(self.config.retry_backoff_seconds)
                continue

        raise LLMGenerationError(
            f"فراخوانی مدل پس از {self.config.max_retries} تلاش ناموفق بود: {last_error}"
        )
