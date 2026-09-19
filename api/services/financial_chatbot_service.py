"""کارگاه «چت‌بات مالی» -- پرسش/پاسخ روی پایگاه‌دانش آماده‌ی پروژه.

این ماژول **منطق پردازش** این کارگاه است و مثل بقیه‌ی سرویس‌های پروژه از هر
دغدغه‌ی HTTP پاک نگه داشته شده است: هیچ ``Request``/``HTTPException``ی این‌جا
نیست. مدیریت گفتگوها و پیام‌ها جدا و در ``api/repositories/chat_sessions.py`` و
``api/repositories/chat_messages.py`` است.

چهار تصمیم طراحی که باید مستند بمانند
-------------------------------------

۱) **کدام پایگاه‌دانش؟** فقط و فقط ``projects.latest_ready_kb_id``. این ماژول
   هرگز جدول ``knowledge_bases`` را برای «حدس‌زدن جدیدترین» پرس‌وجو نمی‌کند و
   کاربر هم اجازه‌ی انتخاب نسخه‌ی قدیمی‌تر را ندارد -- چت‌بات همیشه با
   جدیدترین پایگاه‌دانشِ آماده‌ی همان پروژه حرف می‌زند.

۲) **زمینه‌ی چندنوبتی (multi-turn) -- و اینکه حالا واقعاً روی سرور است.**
   ``rag_chat_module`` عمداً دست‌نخورده می‌ماند و تابع ``chat_ask`` آن فقط یک
   رشته‌ی ``question`` می‌گیرد؛ نه تاریخی می‌پذیرد و نه ``LLMClient.complete``
   آن (``complete(system, user, max_tokens)``) امکان فرستادن یک گفتگوی چندپیامی
   را می‌دهد. بنابراین گزینه‌ی «(ب) دورزدن chat_ask و ساختن گفتگوی چندپیامی»
   بدون تغییر خودِ آن پکیج عملاً ممکن نیست. راه انتخاب‌شده گزینه‌ی «(الف)» است:
   نوبت‌های اخیر گفتگو به یک متن کوتاه تبدیل و **به ابتدای همان ``question``**
   اضافه می‌شوند (``compose_question``)، بنابراین زمینه‌ی گفتگو واقعاً به مدل
   می‌رسد، در حالی که مسیر روتر→بازیابی→پاسخ ``chat_ask`` دست‌نخورده و مشترک
   با بقیه‌ی مصرف‌کننده‌های آن پکیج باقی می‌ماند.

   منبع آن نوبت‌ها **ردیف‌های ذخیره‌شده‌ی ``chat_messages`` است** (نه حافظه‌ی
   مرورگر، که پیش از این فاز بود): ``_recent_history`` آخرین چند پیام همان گفتگو
   را -- با فیلتر سه‌گانه‌ی ``(session_id, project_id, user_id)`` -- می‌خواند.
   نتیجه‌اش این است که یک گفتگوی باز‌شده‌ی قدیمی هم زمینه‌ی واقعی خودش را دارد و
   هم اینکه هیچ کلاینتی نمی‌تواند یک «تاریخچه»ی ساختگی به مدل تزریق کند.

۳) **متن chunk ها از کجا می‌آید؟** از ``rag_ai_adapter.build_readonly_knowledge_base``
   که کش درون‌حافظه‌ای استور را از ``chunks.jsonl`` همان پایگاه‌دانش بازسازی
   می‌کند (توضیح کامل در ``api/services/kb_storage.py``). اگر آن فایل نباشد
   (پایگاه‌دانشی که پیش از این تغییر ساخته شده)، به‌جای پاسخ بی‌معنا یک پیام
   فارسی روشن برگردانده می‌شود.

۴) **پاسخ‌دهی یک کار پس‌زمینه است.** ``answer_question`` همان چیزی است که ترد
   پس‌زمینه اجرا می‌کند (از ``api/jobs/chat_jobs.py``): دو فراخوانی مدل داخل
   ``chat_ask`` چند ثانیه طول می‌کشند، بنابراین درخواست HTTP منتظرشان نمی‌ماند و
   کاربر می‌تواند به کارگاه/گفتگوی دیگری برود. مسئولیت این تابع، برخلاف
   ``ask_with_history`` (که خالص است و هیچ نمی‌نویسد)، *ثبت* نتیجه روی همان ردیف
   ``pending`` است: موفق → ``mark_assistant_message_complete``، هر خطا →
   ``mark_assistant_message_failed`` با پیام فارسی. هیچ مسیری نباید یک پیام را
   برای همیشه در ``pending`` رها کند.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from api.db.base import SessionLocal
from api.db.models import Project
from api.repositories import chat_messages as chat_messages_repo
from api.repositories import chat_sessions as chat_sessions_repo
from api.repositories import projects as projects_repo
from api.services import checklist_kb_service, rag_ai_adapter
from api.services.rag_ai_adapter import FINANCIAL_CHATBOT_WORKSHOP_SLUG as WORKSHOP_SLUG

logger = logging.getLogger(__name__)

__all__ = [
    "ASK_FAILED_FA",
    "ChatbotError",
    "ChatbotUnavailableError",
    "GATED_MESSAGE_FA",
    "NO_INDEX_MESSAGE_FA",
    "PROJECT_GONE_FA",
    "WORKSHOP_SLUG",
    "answer_question",
    "ask_with_history",
    "build_recent_history",
    "build_sheet_catalog",
    "compose_question",
    "is_available",
    "test_connection",
]

# ---------------------------------------------------------------------------
# پیام‌های فارسی حالت‌های «هنوز آماده نیست»
# ---------------------------------------------------------------------------
# نشان داده‌شده در قالبی که هنوز هیچ پایگاه‌دانش آماده‌ای ندارد (نصب تازه).
GATED_MESSAGE_FA = (
    "برای استفاده از چت‌بات مالی، ابتدا باید یک تحلیل در کارگاه «چک‌لیست حسابرسی» "
    "اجرا کنید تا پایگاه‌دانش پروژه ساخته شود."
)
# پایگاه‌دانش هست، اما متن قابل‌جست‌وجویی برایش ذخیره نشده (نسخه‌های پیش از این
# فاز) -- کاربر با یک اجرای دوباره‌ی چک‌لیست مشکل را حل می‌کند.
NO_INDEX_MESSAGE_FA = (
    "پایگاه‌دانش فعلی این پروژه محتوای قابل‌جست‌وجویی ندارد و نمی‌توان از آن پرسش "
    "کرد. لطفاً کارگاه «چک‌لیست حسابرسی» را یک بار دیگر اجرا کنید تا نسخه‌ی جدید "
    "پایگاه‌دانش ساخته شود."
)
EMPTY_QUESTION_FA = "متن پرسش را وارد کنید."
# خطای پیش‌بینی‌نشده‌ی پاسخ‌دهی (سرویس مدل قطع شد، خطای شبکه، ...). پیام فنی
# خام هرگز به کاربر نشان داده نمی‌شود؛ متن کامل در لاگ سرور می‌ماند.
ASK_FAILED_FA = "خطای غیرمنتظره‌ای هنگام پاسخ‌دهی رخ داد. لطفاً دوباره تلاش کنید."
# پروژه/گفتگو در فاصله‌ی بین ثبت پرسش و تولید پاسخ حذف شده است.
PROJECT_GONE_FA = "این گفتگو دیگر در دسترس نیست (پروژه حذف شده است)."

# حداکثر تعداد نوبت‌های تاریخی که به مدل فرستاده می‌شود (آخرین N نوبت). انتخاب
# ۶ نوبت (یعنی حدود سه رفت‌وبرگشت) تعادل بین «فهم سؤال پیگیری» و کوچک نگه‌داشتن
# پنجره‌ی زمینه‌ی مدل است.
MAX_CONTEXT_TURNS = 6
# سقف طول هر نوبت در همان متنِ ساخته‌شده -- حتی اگر کلاینت پیام بلندتری بفرستد.
MAX_CONTEXT_TURN_CHARS = 1200

# راهنمای قالب‌بندی که به همان پرسش اضافه می‌شود: سیستم‌پرامپت داخل
# ``rag_chat_module`` دست‌نخورده می‌ماند، بنابراین تنها اهرم ما برای تشویق مدل
# به خروجی ساخت‌یافته (که UI بتواند به‌صورت Markdown رندر کند) همین خط است.
_FORMATTING_HINT_FA = (
    "پاسخ را به فارسی، روان و ساخت‌یافته بنویس و در صورت مفید بودن از قالب‌بندی "
    "Markdown (تیتر، فهرست نقطه‌ای، جدول، متن پررنگ) استفاده کن. فقط بر اساس "
    "محتوای بازیابی‌شده پاسخ بده و اگر پاسخ در اسناد نیست، صریح بگو که یافت نشد."
)

TEST_SYSTEM_PROMPT = "You are a helpful assistant. Reply with exactly: ok"
TEST_USER_PROMPT = "ping"


class ChatbotError(Exception):
    """خطای این کارگاه با پیام فارسی قابل‌نمایش به کاربر."""


class ChatbotUnavailableError(ChatbotError):
    """این پروژه در وضعیت فعلی نمی‌تواند پرسش پاسخ دهد (بدون پایگاه‌دانش/متن)."""


# ---------------------------------------------------------------------------
# در دسترس بودن
# ---------------------------------------------------------------------------
def is_available(project: Project) -> bool:
    """آیا این پروژه پایگاه‌دانش آماده‌ای دارد که چت‌بات بتواند از آن استفاده کند؟

    تنها معیار، ستون ``latest_ready_kb_id`` است -- همان اشاره‌گری که مستندات این
    پروژه به‌عنوان «تنها منبع حقیقت» برای «پایگاه‌دانش فعلی این پروژه» تعیین
    کرده‌اند. هیچ پرس‌وجوی دیگری روی ``knowledge_bases`` انجام نمی‌شود.
    """
    return bool(getattr(project, "latest_ready_kb_id", None))


# ---------------------------------------------------------------------------
# کاتالوگ فایل‌ها برای مرحله‌ی «روتر» مدل
# ---------------------------------------------------------------------------
def build_sheet_catalog(knowledge_base: Any) -> dict[str, Any]:
    """کاتالوگ «کدام فایل/شیت می‌تواند پاسخ را داشته باشد» برای روتر ``chat_ask``.

    فهرست شیت‌های واقعاً ایندکس‌شده از خودِ پایگاه‌دانش خوانده می‌شود
    (``list_sheets``)، بنابراین مدل هرگز نمی‌تواند یک کلید فایل یا نام شیتِ
    ساختگی انتخاب کند. توصیف هر فایل از ``checklist_kb_service.sheet_descriptions``
    می‌آید -- یعنی دقیقاً همان متنی که هنگام ایندکس‌سازی به ``metadata_text`` هر
    chunk اضافه شده است، پس روتر روی همان واژگانی تصمیم می‌گیرد که برای embedding
    هم استفاده شده و دو مسیر با هم ناهمخوان نمی‌شوند.
    """
    known = checklist_kb_service.sheet_descriptions()
    catalog: dict[str, Any] = {}
    try:
        sheets_by_file = knowledge_base.list_sheets()
    except Exception:  # noqa: BLE001 -- نبود کاتالوگ باید روتر را غیرفعال کند، نه اجرا را بشکند
        logger.warning("could not list sheets for the chatbot router catalog", exc_info=True)
        return {}

    for file_key, sheet_names in (sheets_by_file or {}).items():
        entry = dict(known.get(file_key) or {})
        entry["sheets"] = {str(name): {"description": ""} for name in sheet_names}
        catalog[str(file_key)] = entry
    return catalog


# ---------------------------------------------------------------------------
# ترکیب پرسش با زمینه‌ی گفتگو (تصمیم ۲ در داک‌استرینگ ماژول)
# ---------------------------------------------------------------------------
def _clean_turns(recent_history: Optional[Iterable[Any]]) -> list[tuple[str, str]]:
    """نوبت‌های اخیر را به ``(role, content)`` نرمال می‌کند و به آخرین N محدود می‌کند.

    ورودی می‌تواند مدل‌های Pydantic (``ChatTurn``) یا دیکشنری‌های ساده باشد، چون
    این تابع از چند جا (سرویس/تست) فراخوانی می‌شود. نوبت‌های ناشناخته‌ی role یا
    محتوای خالی بی‌سروصدا حذف می‌شوند -- زمینه‌ی خراب نباید کل پرسش را از کار
    بیندازد.
    """
    turns: list[tuple[str, str]] = []
    for item in recent_history or []:
        if isinstance(item, dict):
            role = str(item.get("role", "")).strip()
            content = str(item.get("content", "")).strip()
        else:
            role = str(getattr(item, "role", "")).strip()
            content = str(getattr(item, "content", "")).strip()
        if role not in ("user", "assistant") or not content:
            continue
        turns.append((role, content[:MAX_CONTEXT_TURN_CHARS]))
    return turns[-MAX_CONTEXT_TURNS:]


def compose_question(question: str, recent_history: Optional[Iterable[Any]] = None) -> str:
    """پرسش نهایی که به ``chat_ask`` داده می‌شود = زمینه‌ی گفتگو + پرسش تازه.

    قالب دنبال همان چیزهایی است که مدل زبانی به‌عنوان «تاریخچه‌ی گفتگو» می‌شناسد
    (برچسب کاربر/دستیار)، بنابراین سؤال‌های پیگیری («و آن یکی چطور؟») با آگاهی از
    نوبت‌های قبلی پاسخ داده می‌شوند. وقتی تاریخی وجود نداشته باشد، متن پرسش
    دست‌نخورده می‌ماند (هیچ سرآغاز اضافه‌ای در مسیر تک‌نوبتی ساده).
    """
    clean_question = (question or "").strip()
    turns = _clean_turns(recent_history)
    if not turns:
        return f"{clean_question}\n\n{_FORMATTING_HINT_FA}"

    lines = ["گفتگوی قبلی:"]
    for role, content in turns:
        speaker = "کاربر" if role == "user" else "دستیار"
        lines.append(f"{speaker}: {content}")
    lines.append("")
    lines.append(f"پرسش جدید کاربر: {clean_question}")
    lines.append("")
    lines.append(_FORMATTING_HINT_FA)
    return "\n".join(lines)


def history_turns_used(recent_history: Optional[Iterable[Any]] = None) -> int:
    """تعداد نوبت‌های تاریخی که واقعاً در متن پرسش گنجانده شده (برای گزارش به UI)."""
    return len(_clean_turns(recent_history))


# ---------------------------------------------------------------------------
# پرسش از پایگاه‌دانش
# ---------------------------------------------------------------------------
def _knowledge_base_for(project: Project):
    """پایگاه‌دانش فقط-خواندنیِ جدیدترین نسخه‌ی آماده‌ی این پروژه.

    ``None`` یعنی متن قابل‌جست‌وجویی برای این پایگاه‌دانش ذخیره نشده است -- تصمیم
    با فراخواننده است که پیام فارسی مناسب را نشان دهد.
    """
    kb_id = project.latest_ready_kb_id
    return rag_ai_adapter.build_readonly_knowledge_base(
        project.user_id, project.id, kb_id
    )


def ask_with_history(
    session: Session,
    project: Project,
    question: str,
    recent_history: Optional[Iterable[Any]] = None,
) -> dict[str, Any]:
    """پرسش از پایگاه‌دانشِ جدیدترین نسخه‌ی آماده‌ی پروژه، با زمینه‌ی گفتگو.

    خروجی یک دیکشنری قابل‌سریال‌سازی نزدیک به ``ChatAnswer.to_dict()`` است:
    ``{answer, confidence, sources, route_reasoning, history_turns_used}``.

    این تابع **خالص است و چیزی نمی‌نویسد** (نه پیام، نه ردیف گفتگو). زمینه‌ی
    گفتگو را فراخواننده از پیام‌های ذخیره‌شده می‌سازد
    (``build_recent_history``) و ثبت نتیجه کار ``answer_question`` است.

    ``session`` فقط برای یک کار لازم است: خواندن تنظیمات هوش مصنوعی همین پروژه
    برای همین کارگاه از طریق ``rag_ai_adapter.build_llm_client`` (همان زنجیره‌ی
    ``ai_settings.resolve_ai_settings``).

    Raises:
        ChatbotUnavailableError: پروژه پایگاه‌دانش آماده ندارد، متن قابل‌جست‌وجو
            برای آن ذخیره نشده، یا متن پرسش خالی است -- همیشه با پیام فارسی.
    """
    if not is_available(project):
        raise ChatbotUnavailableError(GATED_MESSAGE_FA)

    clean_question = (question or "").strip()
    if not clean_question:
        raise ChatbotUnavailableError(EMPTY_QUESTION_FA)

    knowledge_base = _knowledge_base_for(project)
    if knowledge_base is None:
        raise ChatbotUnavailableError(NO_INDEX_MESSAGE_FA)

    from llm_variable_resolver import chat_ask

    llm_client = rag_ai_adapter.build_llm_client(
        session, project.id, workshop_slug=WORKSHOP_SLUG
    )

    composed = compose_question(clean_question, recent_history)
    turns_used = history_turns_used(recent_history)

    logger.info(
        "financial chatbot ask (project=%s, kb=%s, history_turns=%d, question_chars=%d)",
        project.id,
        project.latest_ready_kb_id,
        turns_used,
        len(clean_question),
    )

    answer = chat_ask(
        composed,
        knowledge_base,
        llm_client,
        sheet_catalog=build_sheet_catalog(knowledge_base),
        top_k=3,
        use_router=True,
    )

    payload = answer.to_dict()
    return {
        "answer": str(payload.get("answer", "") or ""),
        "confidence": str(payload.get("confidence", "medium") or "medium"),
        "sources": list(payload.get("sources") or []),
        "route_reasoning": str(payload.get("route_reasoning", "") or ""),
        "history_turns_used": turns_used,
    }


# ---------------------------------------------------------------------------
# زمینه‌ی گفتگو از پیام‌های ذخیره‌شده (تصمیم ۲ در داک‌استرینگ ماژول)
# ---------------------------------------------------------------------------
def build_recent_history(
    session: Session,
    *,
    session_id: str,
    project_id: int,
    user_id: int,
    exclude_message_ids: Iterable[str] = (),
    limit: int = MAX_CONTEXT_TURNS,
) -> list[dict[str, str]]:
    """آخرین نوبت‌های یک گفتگو، از **ردیف‌های ذخیره‌شده** همان گفتگو.

    سه‌گانه‌ی ``(session_id, project_id, user_id)`` به ``list_by_session`` پاس
    می‌شود، پس این تابع ساختاراً نمی‌تواند زمینه‌ی گفتگویی دیگر -- چه در همان
    پروژه و چه در پروژه‌ی کاربر دیگر -- را بخواند.

    ``exclude_message_ids`` معمولاً همان دو ردیفی است که همین حالا برای این پرسش
    ساخته شده‌اند: پرسش تازه‌ی کاربر (که جداگانه به مدل داده می‌شود و نباید دو بار
    بیاید) و ردیف جانشین پاسخ (که هنوز خالی است). ردیف‌های بی‌متن هم در
    ``history_turns`` حذف می‌شوند.
    """
    excluded = set(exclude_message_ids or ())
    rows = chat_messages_repo.list_by_session(
        session, session_id, project_id=project_id, user_id=user_id
    )
    turns = chat_messages_repo.history_turns(row for row in rows if row.id not in excluded)
    return turns[-limit:]


# ---------------------------------------------------------------------------
# کار پس‌زمینه: پاسخ به یک پرسش و ثبت آن روی همان ردیف پیام
# ---------------------------------------------------------------------------
def answer_question(
    *,
    user_id: int,
    project_id: int,
    session_id: str,
    user_message_id: str,
    assistant_message_id: str,
) -> None:
    """تنها کاری که ترد پس‌زمینه اجرا می‌کند: پرسش را پاسخ بده و نتیجه را ثبت کن.

    ورودی‌ها همه شناسه‌اند، نه متن پرسش: خودِ پرسش از همان ردیف ذخیره‌شده خوانده
    می‌شود تا همیشه همان چیزی باشد که کاربر فرستاده و در تاریخچه دیده می‌شود.

    این تابع در یک ترد پس‌زمینه اجرا می‌شود (نشست درخواست HTTP در آن لحظه بسته
    شده است)، بنابراین نشست پایگاه‌داده‌ی خودش را باز می‌کند -- دقیقاً مثل
    ``api/workshops/runs.py::finalize_run``.

    دو نشست، عمداً: فراخوانی مدل چند ثانیه (گاه دقیقه) طول می‌کشد و اگر همان
    تراکنشِ خواندنی تا لحظه‌ی نوشتنِ نتیجه باز بماند، روی SQLite/WAL هر نوشتنِ
    هم‌زمان دیگری (مثلاً ثبت نتیجه‌ی یک کارگاه در پروژه‌ای دیگر) می‌تواند ارتقای
    آن تراکنش به نوشتن را با «پایگاه‌داده مشغول است» رد کند. نوشتن نتیجه در یک
    نشست کوتاه و تازه انجام می‌شود.

    هر مسیر خطا سرانجام پیام را از ``pending`` بیرون می‌آورد؛ هیچ استثنایی از این
    تابع بیرون نمی‌زند که ردیف را معلق بگذارد.
    """
    error: Optional[str] = None
    result: Optional[dict[str, Any]] = None

    with SessionLocal() as db:
        project = projects_repo.get_owned(db, project_id, user_id)
        question_row = chat_messages_repo.get_by_id(
            db,
            user_message_id,
            session_id=session_id,
            project_id=project_id,
            user_id=user_id,
        )
        if project is None or question_row is None:
            # پروژه/گفتگو در همین فاصله حذف شده است. ردیف پاسخ هم قاعدتاً با آن
            # رفته؛ اگر نرفته باشد، پایین برایش خطا ثبت می‌شود.
            logger.warning(
                "chat ask %s aborted: project or question message no longer exists",
                assistant_message_id,
            )
            error = PROJECT_GONE_FA
        else:
            history = build_recent_history(
                db,
                session_id=session_id,
                project_id=project_id,
                user_id=user_id,
                exclude_message_ids=(user_message_id, assistant_message_id),
            )
            try:
                result = ask_with_history(db, project, question_row.content, history)
            except ChatbotUnavailableError as exc:
                # بدون پایگاه‌دانش/متن قابل‌جست‌وجو -- پیام فارسی خودِ سرویس.
                logger.info("chat ask %s unavailable: %s", assistant_message_id, exc)
                error = str(exc) or NO_INDEX_MESSAGE_FA
            except Exception as exc:  # noqa: BLE001 -- هیچ stack trace خامی به کاربر نرسد
                logger.exception("chat ask %s failed", assistant_message_id)
                error = ASK_FAILED_FA

    with SessionLocal() as db:
        if result is not None:
            chat_messages_repo.mark_assistant_message_complete(db, assistant_message_id, result)
            # «آخرین استفاده» گفتگو فقط پس از یک پاسخ موفق تازه می‌شود.
            chat = chat_sessions_repo.get_by_id(
                db, session_id, user_id=user_id, project_id=project_id
            )
            if chat is not None:
                chat_sessions_repo.touch(db, chat)
            logger.info("chat ask %s completed", assistant_message_id)
        else:
            chat_messages_repo.mark_assistant_message_failed(
                db, assistant_message_id, error or ASK_FAILED_FA
            )


# ---------------------------------------------------------------------------
# دکمه‌ی «تست اتصال» فرم تنظیمات هوش مصنوعی این کارگاه
# ---------------------------------------------------------------------------
def test_connection(settings: Any) -> str:
    """یک درخواست کمینه به مدل می‌فرستد و در صورت موفقیت پیام فارسی برمی‌گرداند.

    ورودی یک ``AISettings`` حل‌شده است (همان چیزی که رجیستری کارگاه‌ها به تابع
    تست پاس می‌دهد). کلاینت از ``rag_ai_adapter`` ساخته می‌شود تا این کارگاه هم
    مثل بقیه، تنها از همان یک آداپتور مجاز استفاده کند.

    Raises:
        ChatbotError: با پیام فارسی قابل‌نمایش به کاربر.
    """
    if not getattr(settings, "has_api_key", False):
        raise ChatbotError(
            "برای تست اتصال، ابتدا کلید API را وارد کنید (یا در متغیرهای محیطی سرور "
            "تنظیم کنید)."
        )

    client = rag_ai_adapter.build_llm_client_from_settings(settings)
    try:
        reply = client.complete(TEST_SYSTEM_PROMPT, TEST_USER_PROMPT, max_tokens=16)
    except Exception as exc:  # noqa: BLE001 -- هر شکست باید پیام فارسی بدهد
        raise ChatbotError(str(exc) or "ارتباط با سرویس مدل برقرار نشد.") from exc

    if not str(reply or "").strip():
        raise ChatbotError("سرویس پاسخ خالی برگرداند؛ تنظیمات مدل را بررسی کنید.")
    return f"اتصال موفق بود؛ مدل «{settings.model}» پاسخ داد."
