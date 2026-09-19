"""پرس‌وجوهای «گفتگوهای» کارگاه چت‌بات مالی (جدول ``chat_sessions``).

سبک این ماژول دقیقاً موازی ``api/repositories/workshop_runs.py`` و
``api/repositories/knowledge_bases.py`` است: هر تابع یک عملیات کوچک و صریح انجام
می‌دهد و ``session.commit()`` را خودش صدا می‌زند (این توابع از لایه‌ی روتر/سرویس
فراخوانی می‌شوند، نه از داخل یک تراکنش بزرگ‌تر مشترک).

**این جدول فقط متادیتای گفتگو را نگه می‌دارد** (عنوان و دو زمان)؛ *محتوای* گفتگو
در جدول ``chat_messages`` است و پرس‌وجوهایش در ``api/repositories/chat_messages.py``
-- عمداً جدا، چون آن جدول «داغ‌ترین» مسیر این کارگاه است (بارگذاری تاریخچه و
polling پاسخ) و نباید به این فایل گره بخورد. بنابراین ``delete_by_id`` یک حذف
سختِ *همان یک ردیف* است و **پیام‌های وابسته‌اش** کار ``chat_messages`` است:
``api/workshops/financial_chatbot.py`` پیش از صدا زدن این تابع،
``delete_all_chat_messages_for_session`` را اجرا می‌کند (و ``ON DELETE CASCADE``
هم به‌عنوان تور دوم وجود دارد).

نکته‌ی جداسازی (isolation): یک گفتگو متعلق به یک ``user_id`` **و** یک
``project_id`` مشخص است. همه‌ی توابعی که یک ردیف را برمی‌گردانند/حذف می‌کنند،
هر دو را بررسی می‌کنند و عدم‌تطابق را مثل «پیدا نشد» رفتار می‌کنند (الگوی «۴۰۴
نه ۴۰۳»ی این پروژه، تا وجود گفتگوی کاربر دیگر لو نرود).

``id`` یک UUID رشته‌ای است (``api/db/models.py::new_uuid``)، دقیقاً مثل
``knowledge_bases.id`` -- تا کلید ترکیبی ``(project_id, session_id,
assistant_message_id)`` که job پس‌زمینه‌ی هر پرسش را مشخص می‌کند در کل سیستم
یکتا باشد.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db.models import ChatSession, utcnow

# طول مجاز عنوان یک گفتگو -- همان سقف ستون ``chat_sessions.title``.
TITLE_MAX_LENGTH = 200

DEFAULT_TITLE_FA = "گفتگوی بدون عنوان"


def create(
    session: Session, *, project_id: int, user_id: int, title: Optional[str] = None
) -> ChatSession:
    """ساخت یک گفتگوی جدید (بدون هیچ پیامی؛ پیام‌ها بعداً اضافه می‌شوند).

    شناسه یک UUID تازه است (``ChatSession.id`` پیش‌فرض ``new_uuid`` را دارد)،
    پس هر گفتگو -- حتی برای همان ``(user_id, project_id)`` -- یک ردیف کاملاً
    مستقل با کلید جهانی‌یکتا است.

    عنوان خالی/``None`` به یک عنوان پیش‌فرض فارسی تبدیل می‌شود تا هرگز ردیفی با
    عنوان خالی در فهرست کاربر ظاهر نشود؛ عنوان بلندتر از سقف ستون هم بریده
    می‌شود (خطای ۵۰۰ از پایگاه‌داده بهتر از یک عنوان کوتاه‌شده است).
    """
    cleaned = (title or "").strip()
    if not cleaned:
        cleaned = DEFAULT_TITLE_FA
    if len(cleaned) > TITLE_MAX_LENGTH:
        cleaned = cleaned[:TITLE_MAX_LENGTH]

    chat = ChatSession(project_id=project_id, user_id=user_id, title=cleaned)
    session.add(chat)
    session.commit()
    return chat


def get_by_id(
    session: Session, session_id: str, *, user_id: int, project_id: int
) -> Optional[ChatSession]:
    """گفتگو فقط اگر متعلق به همین کاربر **و** همین پروژه باشد برگردانده می‌شود.

    مثل بقیه‌ی این برنامه، عدم‌تطابق مالکیت «پیدا نشد» در نظر گرفته می‌شود (نه
    یک استثنای مجزا) تا لایه‌ی بالاتر بتواند بدون افشای اطلاعات اضافی ۴۰۴ بدهد.
    """
    chat = session.get(ChatSession, session_id)
    if chat is None or chat.user_id != user_id or chat.project_id != project_id:
        return None
    return chat


def list_by_project(session: Session, project_id: int, *, user_id: int) -> list[ChatSession]:
    """همه‌ی گفتگوهای یک پروژه (و یک کاربر)، «جدیدترین استفاده‌شده» اول.

    مرتب‌سازی بر اساس ``updated_at`` است، نه ``created_at``: چون هر پرسش در یک
    گفتگو آن را تازه می‌کند، گفتگویی که کاربر همین حالا با آن کار کرده بالای
    فهرست می‌ماند. ``id`` به‌عنوان کلید دوم فقط برای قطعی‌بودن ترتیب در زمان
    برابری است (دو گفتگوی ساخته‌شده در یک ثانیه).
    """
    stmt = (
        select(ChatSession)
        .where(ChatSession.project_id == project_id, ChatSession.user_id == user_id)
        .order_by(ChatSession.updated_at.desc(), ChatSession.id.desc())
    )
    return list(session.scalars(stmt))


def touch(session: Session, chat: ChatSession) -> ChatSession:
    """زمان «آخرین استفاده» یک گفتگو را تازه می‌کند (بدون نوشتن هیچ پیامی).

    این تابع را ترد پس‌زمینه‌ی پرسش، بعد از یک پاسخ *موفق* صدا می‌زند -- پس
    گفتگویی که پرسشش شکست خورده، در فهرست کاربر بالا نمی‌آید.
    """
    chat.updated_at = utcnow()
    session.commit()
    return chat


def delete_by_id(session: Session, session_id: str, *, user_id: int, project_id: int) -> bool:
    """حذف سخت *همان یک ردیف* گفتگو (پیام‌هایش جداگانه پاک می‌شوند).

    خروجی ``False`` یعنی ردیف پیدا نشد یا متعلق به این کاربر/پروژه نبود (هیچ
    چیزی حذف نشد). فراخواننده (endpoint حذف گفتگو) مسئول پاک‌کردن پیام‌های همین
    گفتگو پیش از این فراخوانی است -- نگاه کنید به توضیح بالای همین فایل.
    """
    chat = get_by_id(session, session_id, user_id=user_id, project_id=project_id)
    if chat is None:
        return False
    session.delete(chat)
    session.commit()
    return True


def count_by_project(session: Session, project_id: int, *, user_id: int) -> int:
    """تعداد گفتگوهای یک پروژه -- برای برچسب‌های شمارشی در UI."""
    return len(list_by_project(session, project_id, user_id=user_id))


# ---------------------------------------------------------------------------
# حذف در مقیاس «کل پروژه» -- فقط از مسیر حذف پروژه صدا زده می‌شود
# ---------------------------------------------------------------------------
def delete_all_chat_sessions_for_project(
    session: Session, project_id: int, user_id: int
) -> int:
    """**همه‌ی** گفتگوهای یک پروژه (و یک کاربر) را حذف می‌کند و تعدادشان را برمی‌گرداند.

    این تابع دقیقاً مثل ``delete_by_id`` یک حذف سخت است، اما دامنه‌اش کل پروژه
    است، نه یک ردیف. نام‌گذاری عمداً همین را می‌رساند (``all_..._for_project``):
    با یک نگاه به نام تابع باید روشن باشد که این‌جا «همه‌ی یک پروژه» حذف می‌شود و
    نه «یک ردیف». **تنها فراخواننده‌ی مجاز، ``api/services/project_service.py``
    (حذف پروژه) است** -- این تابع هیچ‌گاه به‌عنوان یک endpoint در معرض HTTP قرار
    نمی‌گیرد؛ حذف یک گفتگوی تکی کار ``delete_by_id`` است.

    جفت ``(project_id, user_id)`` هر دو در پرس‌وجو فیلتر می‌شوند (نه فقط
    ``project_id``) -- همان الگوی جداسازی بقیه‌ی این ماژول، تا حتی در صورت یک
    فراخوانی اشتباه هم هیچ ردیفی بیرون از این دامنه پاک نشود.

    پیام‌های وابسته کار ``api/repositories/chat_messages.py`` است: مسیر حذف پروژه
    ابتدا ``delete_all_chat_messages_for_project`` را صدا می‌زند
    (``ON DELETE CASCADE`` هم به‌عنوان تور دوم وجود دارد).
    """
    deleted = (
        session.query(ChatSession)
        .filter(ChatSession.project_id == project_id, ChatSession.user_id == user_id)
        .delete(synchronize_session=False)
    )
    session.commit()
    return int(deleted)
