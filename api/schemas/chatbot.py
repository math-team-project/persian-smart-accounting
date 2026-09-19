"""مدل‌های Pydantic کارگاه «چت‌بات مالی» (گفتگوها، پیام‌های ذخیره‌شده، پرسش).

نکته‌ی مهم درباره‌ی تاریخچه‌ی گفتگو: از این فاز، **پیام‌ها روی سرور ذخیره می‌شوند**
(جدول ``chat_messages``) و زمینه‌ی چندنوبتی از همان ردیف‌های ذخیره‌شده ساخته
می‌شود. بنابراین بدنه‌ی پرسش فقط ``question`` را می‌فرستد: دیگر هیچ کلاینتی
«تاریخچه»ی خودش را همراه پرسش به مدل تزریق نمی‌کند و دو منبع حقیقت وجود ندارد.

پاسخ ``POST .../ask`` هم دیگر خودِ پاسخ مدل نیست: چون پاسخ‌دهی یک کار پس‌زمینه
است (``api/jobs/chat_jobs.py``)، این مسیر فقط شناسه‌ی دو پیام (پرسش کاربر و ردیف
جانشین پاسخ) را برمی‌گرداند و UI با ``GET .../messages/{message_id}`` تا رسیدن
به وضعیت پایانی polling می‌کند.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

# سقف طول پرسش -- همان مرتبه‌ی اندازه‌ی یک پرسش واقعی کاربر و همان سقفی که
# ``api/repositories/chat_messages.py`` هم اعمال می‌کند.
MAX_TEXT_LENGTH = 4000

MessageRole = Literal["user", "assistant"]
MessageStatus = Literal["pending", "complete", "failed"]
Confidence = Literal["high", "medium", "needs_review"]


class CreateChatSessionRequest(BaseModel):
    """بدنه‌ی ساخت گفتگوی جدید. عنوان اختیاری است (پیش‌فرض فارسی سمت سرور)."""

    title: Optional[str] = Field(default=None, max_length=200)


class ChatSessionOut(BaseModel):
    """متادیتای یک گفتگو. ``id`` یک UUID رشته‌ای است (نه عدد)."""

    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ChatSessionListOut(BaseModel):
    sessions: list[ChatSessionOut]


class ChatSourceOut(BaseModel):
    """یک منبع استنادشده در پاسخ (کلید فایل، شیت‌ها، شباهت و یک بریده‌ی متنی)."""

    file_key: str
    sheet_names: list[str] = Field(default_factory=list)
    chunk_id: str = ""
    similarity: float = 0.0
    snippet: str = ""


class ChatMessageOut(BaseModel):
    """یک پیام ذخیره‌شده -- شکل مشترک بارگذاری تاریخچه و polling وضعیت پاسخ.

    همان ردیف ``chat_messages`` است: پرسش کاربر (``status="complete"``)، پاسخ
    آماده (``complete``)، پاسخ در حال تولید (``pending``، با متن خالی) یا پاسخ
    ناموفق (``failed``، با ``error_message`` و بدون متن).
    """

    id: str
    session_id: str
    role: MessageRole
    content: str = ""
    status: MessageStatus = "complete"
    confidence: Optional[Confidence] = None
    sources: list[ChatSourceOut] = Field(default_factory=list)
    route_reasoning: str = ""
    error_message: Optional[str] = None
    created_at: datetime


class ChatMessageListOut(BaseModel):
    """تاریخچه‌ی کامل یک گفتگو، قدیمی‌ترین اول (همان ترتیبی که UI رندر می‌کند)."""

    messages: list[ChatMessageOut]


class ChatAskRequest(BaseModel):
    """بدنه‌ی پرسش: فقط خودِ پرسش.

    زمینه‌ی گفتگو دیگر از سمت کلاینت نمی‌آید؛ سرور همان گفتگو را از
    ``chat_messages`` می‌خواند (نگاه کنید به داک‌استرینگ بالای این فایل).
    """

    question: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)


class ChatAskStartedOut(BaseModel):
    """پاسخ ``POST .../ask`` -- یعنی «پرسش ثبت شد و پاسخ در پس‌زمینه ساخته می‌شود».

    ``user_message_id`` همان ردیفی است که بلافاصله در UI رندر می‌شود و در هر
    بارگذاری بعدی تاریخچه برمی‌گردد؛ ``assistant_message_id`` شناسه‌ی ردیفی است که
    باید تا رسیدن به ``complete``/``failed`` polling شود.
    """

    user_message_id: str
    assistant_message_id: str
    status: MessageStatus = "pending"
