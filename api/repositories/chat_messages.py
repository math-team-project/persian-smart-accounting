"""پرس‌وجوهای محتوای گفتگوهای کارگاه چت‌بات مالی (جدول ``chat_messages``).

سبک این ماژول دقیقاً موازی ``api/repositories/chat_sessions.py`` و
``api/repositories/knowledge_bases.py`` است: هر تابع یک عملیات کوچک و صریح انجام
می‌دهد و ``session.commit()`` را خودش صدا می‌زند.

**جداسازی (isolation):** هر ردیف پیام، سه‌گانه‌ی ``session_id``/``project_id``/
``user_id`` را روی خودش دارد (نگاه کنید به ``api/db/models.py::ChatMessage``)، پس
هر پرس‌وجوی این ماژول یک پرس‌وجوی *تک‌جدولی و سه‌دامنه‌ای* است. عدم‌تطابق مثل
«پیدا نشد» رفتار می‌کند (الگوی «۴۰۴ نه ۴۰۳» این پروژه)، نه مثل «ممنوع».

**دو استثنا که عمداً شناسه‌محور و بدون دامنه‌اند** (``mark_assistant_message_complete``
و ``mark_assistant_message_failed``): این دو فقط از *ترد پس‌زمینه‌ی همان پرسش*
صدا زده می‌شوند و هرگز از هیچ endpoint ای قابل‌دسترسی نیستند (تنها id ای که دارند
همان id ای است که خودشان چند لحظه قبل ساخته‌اند). بقیه‌ی توابع -- همان‌هایی که
HTTP می‌بیند -- همیشه سه‌دامنه‌ای‌اند.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db.models import ChatMessage, new_uuid, utcnow

logger = logging.getLogger(__name__)

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"

STATUS_PENDING = "pending"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"

# سقف طول متن یک پیام -- همان سقفی که ``api/schemas/chatbot.py`` روی پرسش کاربر
# اعمال می‌کند؛ این‌جا فقط یک محافظ پایگاه‌داده است.
MAX_CONTENT_LENGTH = 4000
# سقف طول پیام خطای ذخیره‌شده (خطاهای سرویس‌های بیرونی گاهی JSON بلند هستند).
MAX_ERROR_LENGTH = 2000


# ---------------------------------------------------------------------------
# زمان‌مهر یکنوا (monotonic) برای ترتیب قطعی پیام‌ها
# ---------------------------------------------------------------------------
# چرا لازم است: ترتیب نمایش پیام‌ها بر اساس ``created_at`` است، اما ساعت سیستم
# (به‌ویژه روی Windows) دقت کافی ندارد و ممکن است دو پیامِ ساخته‌شده در یک درخواست
# (پرسش کاربر و ردیف جانشین پاسخ) *دقیقاً* یک زمان بگیرند. در آن حالت هیچ ترتیب
# قطعی‌ای وجود نداشت و تاریخچه می‌توانست جابه‌جا نمایش داده شود. این شمارنده
# تضمین می‌کند هر پیام تازه، زمانی اکیداً بزرگ‌تر از پیام قبلی بگیرد -- بدون
# افزودن هیچ ستون ترتیبی به جدول.
_created_at_lock = threading.Lock()
_last_created_at: Optional[datetime] = None


def _next_created_at() -> datetime:
    """زمان فعلی، اما همیشه اکیداً بزرگ‌تر از آخرین زمان صادرشده در این فرآیند."""
    global _last_created_at
    with _created_at_lock:
        moment = utcnow()
        if _last_created_at is not None and moment <= _last_created_at:
            moment = _last_created_at + timedelta(microseconds=1)
        _last_created_at = moment
        return moment


# ---------------------------------------------------------------------------
# ساخت پیام
# ---------------------------------------------------------------------------
def create_user_message(
    session: Session,
    *,
    session_id: str,
    project_id: int,
    user_id: int,
    content: str,
) -> ChatMessage:
    """پرسش کاربر را ذخیره می‌کند (``role="user"``، ``status="complete"``).

    این ردیف همان چیزی است که هم بلافاصله در UI رندر می‌شود و هم در هر بارگذاری
    بعدی تاریخچه برمی‌گردد، پس پرسش کاربر هم «آنی» دیده می‌شود و هم پس از refresh
    باقی می‌ماند.
    """
    text = (content or "").strip()
    if not text:
        raise ValueError("content of a user message must not be empty")
    if len(text) > MAX_CONTENT_LENGTH:
        text = text[:MAX_CONTENT_LENGTH]

    row = ChatMessage(
        id=new_uuid(),
        session_id=session_id,
        project_id=project_id,
        user_id=user_id,
        role=ROLE_USER,
        content=text,
        status=STATUS_COMPLETE,
        created_at=_next_created_at(),
    )
    session.add(row)
    session.commit()
    return row


def create_pending_assistant_message(
    session: Session, *, session_id: str, project_id: int, user_id: int
) -> ChatMessage:
    """ردیف جانشین پاسخ دستیار را با ``status="pending"`` و متن خالی می‌سازد.

    این ردیف *پیش از* شروع فراخوانی مدل ساخته می‌شود تا پاسخ HTTP بلافاصله یک
    شناسه برگرداند و UI از همان لحظه بتواند وضعیت را polling کند. محتوایش بعداً
    با ``mark_assistant_message_complete`` پر می‌شود (یا با
    ``mark_assistant_message_failed`` به خطا می‌رود).
    """
    row = ChatMessage(
        id=new_uuid(),
        session_id=session_id,
        project_id=project_id,
        user_id=user_id,
        role=ROLE_ASSISTANT,
        content="",
        status=STATUS_PENDING,
        created_at=_next_created_at(),
    )
    session.add(row)
    session.commit()
    return row


# ---------------------------------------------------------------------------
# پایان کار ترد پس‌زمینه
# ---------------------------------------------------------------------------
def mark_assistant_message_complete(
    session: Session, message_id: str, answer_dict: dict[str, Any]
) -> Optional[ChatMessage]:
    """پاسخ تولیدشده را روی ردیف ``pending`` می‌نویسد و آن را ``complete`` می‌کند.

    ``answer_dict`` همان شکل خروجی ``financial_chatbot_service.ask_with_history``
    است (``answer``/``confidence``/``sources``/``route_reasoning``).
    ``None`` یعنی ردیف دیگر وجود ندارد (مثلاً پروژه/گفتگو در همین فاصله حذف شده)
    -- این حالت خطا نیست و نباید ترد پس‌زمینه را بیندازد.
    """
    row = session.get(ChatMessage, message_id)
    if row is None or row.role != ROLE_ASSISTANT:
        logger.warning("assistant message %s vanished before it could be completed", message_id)
        return None

    sources = list(answer_dict.get("sources") or [])
    row.content = str(answer_dict.get("answer") or "")
    row.confidence = str(answer_dict.get("confidence") or "") or None
    row.sources = json.dumps(sources, ensure_ascii=False) if sources else None
    row.route_reasoning = str(answer_dict.get("route_reasoning") or "") or None
    row.error_message = None
    row.status = STATUS_COMPLETE
    session.commit()
    return row


def mark_assistant_message_failed(
    session: Session, message_id: str, error_message: str
) -> Optional[ChatMessage]:
    """ردیف پاسخ را ``failed`` می‌کند و پیام فارسی خطا را رویش می‌گذارد.

    هرگز نباید یک پیام برای همیشه در ``pending`` بماند: هر مسیر خطا در ترد
    پس‌زمینه (خطای سرویس، نبود متن قابل‌جست‌وجو، حذف‌شدن پروژه در میانه‌ی کار) در
    نهایت همین تابع را صدا می‌زند، پس UI همیشه به یک وضعیت پایانی می‌رسد.
    """
    row = session.get(ChatMessage, message_id)
    if row is None or row.role != ROLE_ASSISTANT:
        logger.warning("assistant message %s vanished before it could be failed", message_id)
        return None

    text = str(error_message or "").strip() or "پاسخ‌دهی ناموفق بود."
    row.error_message = text[:MAX_ERROR_LENGTH]
    row.status = STATUS_FAILED
    session.commit()
    return row


def mark_pending_messages_failed(
    session: Session, *, error_message: str, project_id: Optional[int] = None
) -> int:
    """هر پیام ``pending`` باقی‌مانده را ناموفق علامت می‌زند (پاک‌سازی راه‌اندازی).

    وضعیت job های پس‌زمینه در حافظه‌ی همین فرآیند است؛ هر ردیف ``pending`` که پس
    از راه‌اندازی مجدد سرور باقی بماند متعلق به فرآیندی است که دیگر وجود ندارد و
    در غیر این صورت تا ابد «در حال پاسخ‌گویی» می‌ماند. قرینه‌ی دقیق
    ``api/workshops/runs.py::mark_orphaned_runs`` برای جدول پیام‌هاست.

    ``project_id`` اختیاری فقط دامنه را محدود می‌کند؛ فراخوانی راه‌اندازی مقدار
    نمی‌دهد (همه‌ی پروژه‌ها). خروجی تعداد ردیف‌های علامت‌خورده است.
    """
    stmt = select(ChatMessage).where(ChatMessage.status == STATUS_PENDING)
    if project_id is not None:
        stmt = stmt.where(ChatMessage.project_id == project_id)
    rows = list(session.scalars(stmt))
    for row in rows:
        row.status = STATUS_FAILED
        row.error_message = error_message[:MAX_ERROR_LENGTH]
    if rows:
        session.commit()
        logger.warning("marked %d pending chat message(s) as failed", len(rows))
    return len(rows)


# ---------------------------------------------------------------------------
# خواندن (همه سه‌دامنه‌ای)
# ---------------------------------------------------------------------------
def get_by_id(
    session: Session,
    message_id: str,
    *,
    session_id: str,
    project_id: int,
    user_id: int,
) -> Optional[ChatMessage]:
    """یک پیام فقط اگر هم‌زمان به همین گفتگو، همین پروژه **و** همین کاربر متعلق باشد.

    این تابع پشت endpoint polling است؛ هر سه شرط در همان یک پرس‌وجو بررسی می‌شوند
    تا نه با یک شناسه‌ی حدسی و نه از مسیر پروژه/گفتگوی دیگر نتوان پیامی خواند.
    """
    stmt = select(ChatMessage).where(
        ChatMessage.id == message_id,
        ChatMessage.session_id == session_id,
        ChatMessage.project_id == project_id,
        ChatMessage.user_id == user_id,
    )
    return session.scalars(stmt).first()


def list_by_session(
    session: Session, session_id: str, *, project_id: int, user_id: int
) -> list[ChatMessage]:
    """همه‌ی پیام‌های یک گفتگو، قدیمی‌ترین اول -- همان چیزی که یک گفتگوی باز را می‌سازد.

    ترتیب بر اساس ``created_at`` است و در زمان برابری (که عملاً رخ نمی‌دهد، چون
    ``_next_created_at`` اکیداً افزایشی است) ``id`` به‌عنوان کلید دوم فقط برای
    قطعی‌بودن ترتیب می‌آید.
    """
    stmt = (
        select(ChatMessage)
        .where(
            ChatMessage.session_id == session_id,
            ChatMessage.project_id == project_id,
            ChatMessage.user_id == user_id,
        )
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
    )
    return list(session.scalars(stmt))


def parsed_sources(row: ChatMessage) -> list[dict[str, Any]]:
    """فهرست منابع یک پیام دستیار را از متن JSON ذخیره‌شده بازمی‌گرداند.

    تنها جایی که می‌داند ``chat_messages.sources`` یک *متن JSON* است همین ماژول
    است؛ لایه‌ی روتر فقط این تابع را صدا می‌زند. متن خراب/خالی به یک فهرست خالی
    تبدیل می‌شود (نمایش تاریخچه نباید به‌خاطر یک ردیف ناقص بشکند).
    """
    raw = row.sources
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("chat message %s has unparsable sources JSON", row.id)
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def history_turns(rows: Iterable[ChatMessage]) -> list[dict[str, str]]:
    """ردیف‌های پیام را به نوبت‌های ``{role, content}`` قابل‌فرستادن به سرویس تبدیل می‌کند.

    ردیف‌های بی‌متن (پاسخ ``pending`` یا پاسخ ``failed`` که محتوایی تولید نکرده)
    بی‌سروصدا حذف می‌شوند -- زمینه‌ی گفتگو نباید یک ردیف خالی را به مدل بدهد.
    """
    turns: list[dict[str, str]] = []
    for row in rows:
        content = (row.content or "").strip()
        if row.role not in (ROLE_USER, ROLE_ASSISTANT) or not content:
            continue
        turns.append({"role": row.role, "content": content})
    return turns


# ---------------------------------------------------------------------------
# حذف
# ---------------------------------------------------------------------------
def delete_all_chat_messages_for_session(
    session: Session, session_id: str, project_id: int, user_id: int
) -> int:
    """**همه‌ی** پیام‌های یک گفتگو را حذف می‌کند (پیش از حذف خودِ ردیف گفتگو).

    با اینکه ``ON DELETE CASCADE`` در سطح پایگاه‌داده وجود دارد، حذف صریح دقیقاً
    همان سیاست ``delete_all_knowledge_bases_for_project`` است: هیچ ردیف یتیمی
    باقی نمی‌ماند، حتی اگر اجرای کلیدهای خارجی در SQLite روزی خاموش شود.
    کلید سه‌گانه (نه فقط ``session_id``) همان الگوی جداسازی بقیه‌ی این ماژول است.
    """
    deleted = (
        session.query(ChatMessage)
        .filter(
            ChatMessage.session_id == session_id,
            ChatMessage.project_id == project_id,
            ChatMessage.user_id == user_id,
        )
        .delete(synchronize_session=False)
    )
    session.commit()
    return int(deleted)


def delete_all_chat_messages_for_project(session: Session, project_id: int, user_id: int) -> int:
    """**همه‌ی** پیام‌های گفتگوهای یک پروژه را حذف می‌کند.

    نام‌گذاری عمداً همین را می‌رساند (``all_..._for_project``): این تابع دامنه‌اش
    کل پروژه است، نه یک گفتگو. **تنها فراخواننده‌ی مجاز،
    ``api/services/project_service.py`` (حذف پروژه) است** و هیچ endpoint ای آن را
    در معرض HTTP نمی‌گذارد. جفت ``(project_id, user_id)`` هر دو فیلتر می‌شوند تا
    حتی در صورت یک فراخوانی اشتباه هم هیچ ردیفی بیرون از این دامنه پاک نشود.
    """
    deleted = (
        session.query(ChatMessage)
        .filter(ChatMessage.project_id == project_id, ChatMessage.user_id == user_id)
        .delete(synchronize_session=False)
    )
    session.commit()
    return int(deleted)
