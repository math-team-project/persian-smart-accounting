"""مدل‌های Pydantic کارگاه «چت‌بات مالی» (گفتگوهای متادیتا-محور + پرسش/پاسخ).

نکته‌ی مهم درباره‌ی ``recent_history``: این فهرست **هیچ‌گاه ذخیره نمی‌شود**. فقط
در بدنه‌ی همان یک درخواست HTTP وجود دارد، به سرویس پاس داده می‌شود تا به پرسش
مدل اضافه شود، و بعد از پاسخ دور ریخته می‌شود. مرورگر هم آن را فقط در حافظه‌ی
همان صفحه نگه می‌دارد (نه در ``localStorage``). سقف‌های این فایل برای همین
هستند: یک payload بزرگ یا بی‌پایان نباید به مدل فرستاده شود.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

# سقف تعداد نوبت‌های تاریخی که سرور می‌پذیرد. مرورگر معمولاً ۶ تا ۱۰ نوبت آخر
# را می‌فرستد؛ این سقف محافظ سرور است (نه قرارداد UI) تا یک کلاینت دست‌کاری‌شده
# نتواند پنجره‌ی زمینه‌ی مدل را بی‌نهایت کند.
MAX_HISTORY_TURNS = 24
# سقف طول هر پیام تاریخی/پرسش -- همان مرتبه‌ی اندازه‌ی یک پرسش واقعی کاربر.
MAX_TEXT_LENGTH = 4000


class ChatTurn(BaseModel):
    """یک نوبت گفتگو در همان درخواست -- زودگذر و ذخیره‌نشدنی."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)


class CreateChatSessionRequest(BaseModel):
    """بدنه‌ی ساخت گفتگوی جدید. عنوان اختیاری است (پیش‌فرض فارسی سمت سرور)."""

    title: Optional[str] = Field(default=None, max_length=200)


class ChatSessionOut(BaseModel):
    """متادیتای یک گفتگو -- تنها چیزی که این کارگاه ذخیره می‌کند."""

    id: int
    title: str
    created_at: datetime
    updated_at: datetime


class ChatSessionListOut(BaseModel):
    sessions: list[ChatSessionOut]


class ChatAskRequest(BaseModel):
    """بدنه‌ی پرسش: خودِ پرسش + زمینه‌ی گفتگوی زنده (اختیاری، ذخیره نمی‌شود)."""

    question: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    recent_history: list[ChatTurn] = Field(
        default_factory=list, max_length=MAX_HISTORY_TURNS
    )


class ChatSourceOut(BaseModel):
    """یک منبع استنادشده در پاسخ (کلید فایل، شیت‌ها، شباهت و یک بریده‌ی متنی)."""

    file_key: str
    sheet_names: list[str] = Field(default_factory=list)
    chunk_id: str = ""
    similarity: float = 0.0
    snippet: str = ""


class ChatAskOut(BaseModel):
    """پاسخ یک پرسش -- شکل آن از ``ChatAnswer.to_dict()`` پکیج RAG می‌آید."""

    answer: str
    confidence: Literal["high", "medium", "needs_review"] = "medium"
    sources: list[ChatSourceOut] = Field(default_factory=list)
    route_reasoning: str = ""
    # تعداد نوبت‌های تاریخی که واقعاً به مدل فرستاده شد -- برای شفافیت/اشکال‌زدایی
    # سمت UI (کاربر می‌بیند سامانه «چند پیام قبلی» را در نظر گرفته است).
    history_turns_used: int = 0
