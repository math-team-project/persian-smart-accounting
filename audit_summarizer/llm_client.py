"""
llm_client.py
----------------
Thin, configurable wrapper around the api.b.ai chat-completions endpoint
(OpenAI-compatible `/v1/chat/completions` shape), plus the prompt-construction
logic that turns raw audit-report text into a request for a plain-language,
committee-ready summary (Persian by default).

The request skeleton mirrors the one you supplied. Only the *values* (model,
temperature, max_tokens, streaming on/off, prompt text) are meant to be
adjusted - the shape of the payload should not need to change for normal use.

Security note: never hardcode the API key in source. Set it via the
B_AI_API_KEY environment variable (or pass LLMConfig(api_key=...) explicitly
at call time from your own secret store).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
import os
from dotenv import load_dotenv
import requests

B_AI_API_URL = "https://api.b.ai/v1/chat/completions"
# B_AI_API_URL = "https://openrouter.ai/api/v1/chat/completions"


# Reasonable default. Override via LLMConfig(model=...)
DEFAULT_MODEL = "mimo-v2.5"
# DEFAULT_MODEL = "minimax/minimax-m3:free"


@dataclass
class LLMConfig:
    """Everything a caller is expected to tune."""

    model: str = DEFAULT_MODEL
    temperature: float = 0.3          # low-ish: audit content should stay factual, not creative
    max_tokens: int = 20000            # reasoning models (e.g. glm/deepseek "thinking" variants) burn
                                       # part of this budget on internal reasoning before writing the
                                       # final answer - too low a value can leave zero tokens for output
    stream: bool = True
    api_key: str | None = None        # falls back to B_AI_API_KEY env var
    timeout_s: int = 450
    extra_params: dict | None = None  # provider-specific extras merged into the payload as-is,
                                       # e.g. {"thinking": {"type": "disabled"}} if api.b.ai supports
                                       # turning off extended reasoning for a given model - check their docs


class LLMClientError(Exception):
    """Raised for network, auth, or malformed-response failures."""


def build_summary_prompt(
    report_text: str,
    *,
    language: str = "fa",
    audience: str = "اعضای هیات مدیره / کمیته که پیش‌زمینه حسابداری ندارند",
    organization_name: str | None = None,
) -> str:
    """
    Build the user-turn prompt sent to the model. Kept as a standalone function
    (rather than buried in call_llm) so it can be unit-tested and tuned without
    touching the networking code.
    """
    lang_instruction = (
        "پاسخ را کاملاً به زبان فارسی، روان و رسمی بنویس."
        if language == "fa"
        else "Respond entirely in clear, formal English."
    )
    org_line = f"نام سازمان مورد حسابرسی: {organization_name}\n" if organization_name else ""

    return f"""شما دستیار تخصصی خلاصه‌سازی گزارش‌های حسابرسی برای جلسات کمیته/هیات‌مدیره هستید.
متن زیر یک گزارش حسابرسی (یا سند مالی مرتبط) است. آن را برای مخاطبی که «{audience}» است خلاصه کن.

{org_line}{lang_instruction}

قوانین محتوایی (حتماً رعایت شود):
1. از اصطلاحات فنی حسابداری بدون توضیح استفاده نکن؛ هر اصطلاح تخصصی را در یک جمله ساده توضیح بده.
2. نوع اظهارنظر حسابرس (مقبول، مشروط، مردود، عدم اظهارنظر) را در همان ابتدا و به‌وضوح ذکر کن.
3. یافته‌ها و بندهای «شرط» یا «تاکید بر مطلب خاص» را به‌صورت فهرست، به ترتیب اهمیت مالی (مبلغ ریالی/دلاری، در صورت وجود) ذکر کن.
4. برای هر یافته: (الف) موضوع چیست، (ب) چرا اهمیت دارد، (ج) چه ریسکی برای سازمان دارد.
5. یک بخش جداگانه با عنوان دقیق "موارد نیازمند بررسی و تصمیم‌گیری در جلسه" بساز: فهرستی عملی و مشخص از سوالاتی که اعضای کمیته باید بپرسند یا تصمیماتی که باید بگیرند (مثلاً: تایید/رد یک مصوبه، درخواست پیگیری از مدیریت، تعیین مهلت رفع یک ایراد).
6. اگر مبالغ، تاریخ‌ها یا مواد قانونی خاصی در متن آمده، آن‌ها را دقیق و بدون تغییر منتقل کن - چیزی را حدس نزن یا اختراع نکن.
7. اگر بخشی از متن مبهم یا ناقص است، آن را به‌صراحت به‌عنوان «نیاز به شفاف‌سازی» علامت بزن؛ وانمود نکن که اطلاعات کامل است.
8. لحن باید رسمی، بی‌طرف و مناسب سند رسمی جلسه باشد - نه محاوره‌ای و نه اغراق‌آمیز.

ساختار خروجی را دقیقاً به این شکل ارائه بده (از همین سرتیترها استفاده کن):

## خلاصه مدیریتی
## نوع اظهارنظر حسابرس
## یافته‌های کلیدی
## ریسک‌ها و آثار مالی
## موارد نیازمند بررسی و تصمیم‌گیری در جلسه
## نکات نیازمند شفاف‌سازی (در صورت وجود)

متن گزارش حسابرسی:
\"\"\"
{report_text}
\"\"\"
"""


def call_llm(
    prompt: str,
    config: LLMConfig | None = None,
    *,
    system: str | None = None,
    logger: Callable[[str], None] | None = None,
) -> str:
    """
    Send `prompt` to the api.b.ai chat-completions endpoint and return the
    concatenated text of the response. Supports both streaming and
    non-streaming modes.

    `logger`, if given, receives human-readable progress/debug strings (e.g.
    the raw JSON response) in addition to the normal `print()` calls, so
    callers such as a Streamlit dashboard can capture them without relying on
    stdout redirection (which is not thread-safe for background jobs).
    """
    config = config or LLMConfig()
    load_dotenv()
    api_key = os.getenv("API_KEY_B_AI")
    if not api_key:
        raise ValueError("API_KEY_B_AI در فایل env پیدا نشد!")
    print(f"کلید با موفقیت بارگذاری شد (فقط ۵ کاراکتر اول نشان داده می‌شود): {api_key[:5]}...")

    # api_key = config.api_key or os.environ.get("B_AI_API_KEY")
    # if not api_key:
    #     raise LLMClientError(
    #         "کلید API یافت نشد. متغیر محیطی B_AI_API_KEY را تنظیم کنید یا "
    #         "آن را در LLMConfig(api_key=...) پاس دهید."
    #     )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    messages = []
    if system:
        # This provider follows the OpenAI-style schema, where system
        # instructions are just another message with role="system".
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": config.model,
        "messages": messages,
        "stream": config.stream,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
    }
    if config.extra_params:
        payload.update(config.extra_params)

    if config.stream:
        return _call_streaming(headers, payload, config.timeout_s, logger)
    return _call_non_streaming(headers, payload, config.timeout_s, logger)


def _call_non_streaming(
    headers: dict, payload: dict, timeout_s: int, logger: Callable[[str], None] | None = None
) -> str:
    try:
        response = requests.post(
            B_AI_API_URL, headers=headers, json=payload, timeout=timeout_s
        )
    except requests.RequestException as e:
        raise LLMClientError(f"خطای شبکه هنگام تماس با API: {e}") from e

    if response.status_code != 200:
        raise LLMClientError(
            f"API خطا برگرداند (status={response.status_code}): {response.text[:800]}"
        )

    # requests decodes .json() straight from the raw bytes (utf-8-aware),
    # so this is safe regardless of any (possibly wrong) Content-Type charset.
    data = response.json()
    print(data)
    if logger:
        logger(f"پاسخ کامل مدل (JSON): {json.dumps(data, ensure_ascii=False)}")
    return _extract_text_from_choices(data.get("choices", []))


def _call_streaming(
    headers: dict, payload: dict, timeout_s: int, logger: Callable[[str], None] | None = None
) -> str:
    """Consume an OpenAI-style text/event-stream response and reassemble the full text."""
    try:
        response = requests.post(
            B_AI_API_URL, headers=headers, json=payload, timeout=timeout_s, stream=True
        )
    except requests.RequestException as e:
        raise LLMClientError(f"خطای شبکه هنگام تماس با API (stream): {e}") from e

    if response.status_code != 200:
        raise LLMClientError(
            f"API خطا برگرداند (status={response.status_code}): {response.text[:800]}"
        )

    # IMPORTANT: requests guesses the response encoding from the Content-Type
    # header (falling back to a Latin-1-ish default per RFC 2616) when
    # iterating decoded text lines. If the server doesn't send an explicit
    # charset (common for SSE/event-stream responses), `iter_lines(decode_unicode=True)`
    # can silently mis-decode multi-byte UTF-8 text (e.g. Persian) into
    # mojibake such as "Ø®ÙØ§ØµÛ". Forcing utf-8 here fixes that at the root.
    response.encoding = "utf-8"

    chunks: list[str] = []
    saw_reasoning = False
    finish_reason: str | None = None

    for raw_line in response.iter_lines(decode_unicode=True):
        if not raw_line or not raw_line.startswith("data:"):
            continue
        data_str = raw_line[len("data:"):].strip()
        if data_str in ("", "[DONE]"):
            continue
        try:
            event = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        for choice in event.get("choices", []):
            delta = choice.get("delta", {})
            content = delta.get("content")
            if content:
                chunks.append(content)
            if delta.get("reasoning_content"):
                saw_reasoning = True
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]

    full_text = "".join(chunks)
    if logger:
        logger(f"دریافت پاسخ استریم مدل: {len(full_text)} کاراکتر (finish_reason={finish_reason}).")
    if not full_text:
        _raise_empty_response_error(finish_reason, saw_reasoning)
    return full_text


def _extract_text_from_choices(choices: list[dict]) -> str:
    texts = []
    saw_reasoning = False
    finish_reason: str | None = None
    for choice in choices:
        message = choice.get("message", {})
        content = message.get("content")
        if content:
            texts.append(content)
        if message.get("reasoning_content"):
            saw_reasoning = True
        if choice.get("finish_reason"):
            finish_reason = choice["finish_reason"]

    combined = "\n".join(t for t in texts if t)
    if not combined.strip():
        _raise_empty_response_error(finish_reason, saw_reasoning)
    return combined


def _raise_empty_response_error(finish_reason: str | None, saw_reasoning: bool) -> None:
    """
    Raise a specific, actionable error for the common failure mode where a
    "thinking"/reasoning model (e.g. glm-*, deepseek-*-thinking) spends its
    entire max_tokens budget on internal reasoning and truncates
    (finish_reason == "length") before writing anything into the final
    answer field.
    """
    if finish_reason == "length" and saw_reasoning:
        raise LLMClientError(
            "مدل کل سهمیه max_tokens را صرف «فکر کردن» (reasoning_content) کرد و به پاسخ نهایی "
            "نرسید (finish_reason='length', content خالی). این معمولاً وقتی رخ می‌دهد که متن ورودی "
            "پیچیده/نامنظم باشد (مثلاً یک PDF که متنش به‌درستی استخراج نشده) یا max_tokens برای یک "
            "مدل reasoning خیلی کم باشد. راه‌حل‌ها: (۱) از استخراج صحیح متن ورودی مطمئن شوید، "
            "(۲) max_tokens را به‌طور قابل‌توجه افزایش دهید (برای مدل‌های reasoning معمولاً ۸۰۰۰ به بالا لازم است)، "
            "(۳) اگر پروایدر پارامتری برای غیرفعال‌کردن حالت thinking دارد، آن را در "
            "LLMConfig(extra_params=...) تنظیم کنید، یا (۴) از یک مدل غیر-reasoning استفاده کنید."
        )
    if finish_reason == "length":
        raise LLMClientError(
            "پاسخ مدل قبل از تکمیل قطع شد (finish_reason='length'). max_tokens را افزایش دهید."
        )
    raise LLMClientError("پاسخ مدل حاوی متن نبود.")
