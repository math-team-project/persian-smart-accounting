"""کارگاه «چت‌بات مالی» به‌عنوان یک ورودی رجیستری.

ساختار این ماژول از ``api/workshops/budget_analysis.py`` پیروی می‌کند (همان الگوی
``router`` + ``render_page`` + ``register(WorkshopDefinition(...))``)، اما **این
کارگاه در تاریخچه‌ی اجراها نیست**: هیچ ``workshop_runs``ی ساخته نمی‌شود و هیچ
job ای در ``JobManager`` ثبت نمی‌شود (پس در پنل «کارهای در جریان» و تاریخچه‌ی
پروژه ظاهر نمی‌شود -- و این درست است: تاریخچه‌ی این کارگاه *گفتگوها* هستند، نه
اجراها).

پرسش در این کارگاه **یک کار پس‌زمینه** است (چند ثانیه فراخوانی مدل)، اما نه از
طریق ``JobManager`` -- از یک اجراکننده‌ی کوچک و مخصوص خودش
(``api/jobs/chat_jobs.py``) که کلیدش دقیقاً ``(project_id, session_id,
assistant_message_id)`` است. سه تفاوت این کارگاه با بقیه‌ی کارگاه‌ها:

* چهار callable اجباری رجیستری (``estimate_progress``/``collect_result``/
  ``has_download``/``summary_chips_fa``) با پیاده‌سازی‌های حداقلی و بی‌اثر پر
  شده‌اند، چون هیچ ردیف اجرایی وجود ندارد که آن‌ها روی آن صدا زده شوند.
* مدیریت گفتگوها در ``api/repositories/chat_sessions.py`` و مدیریت پیام‌ها
  (تاریخچه‌ی ماندگار پرسش/پاسخ) در ``api/repositories/chat_messages.py`` است؛
  منطق پردازش/پرسش در ``api/services/financial_chatbot_service.py``.
* مسیر ``POST .../ask`` بلافاصله (۲۰۲) برمی‌گردد و فقط شناسه‌ی دو پیام را می‌دهد؛
  پاسخ واقعی با polling روی ``GET .../messages/{message_id}`` می‌آید. این همان
  «در پس‌زمینه ادامه بده، حتی اگر کاربر برود» است: کار سمت سرور به عمر صفحه‌ی
  مرورگر گره نخورده است.

چون این کارگاه واقعاً تنظیمات هوش مصنوعی پروژه را در زمان اجرا اعمال می‌کند
(``rag_ai_adapter.build_llm_client``)، ``settings_applied=True`` و یک
``test_connection`` دارد؛ بنابراین فرم تنظیمات آن به‌صورت خودکار در صفحه‌ی پروژه
ظاهر می‌شود (``workshops_with_settings()``) بدون هیچ تغییری در قالب‌ها.
"""
from __future__ import annotations

import logging
from functools import partial
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from api.auth.deps import require_api_project
from api.db.base import get_session
from api.db.models import Project, User
from api.jobs.chat_jobs import chat_ask_jobs
from api.repositories import chat_messages as messages_repo
from api.repositories import chat_sessions as sessions_repo
from api.schemas.chatbot import (
    ChatAskRequest,
    ChatAskStartedOut,
    ChatMessageListOut,
    ChatMessageOut,
    ChatSessionListOut,
    ChatSessionOut,
    ChatSourceOut,
    CreateChatSessionRequest,
)
from api.services import financial_chatbot_service as chatbot
from api.templating import templates
from api.workshops.pages import workshop_page_context
from api.workshops.registry import ResultArtifact, WorkshopDefinition, get as get_workshop, register

logger = logging.getLogger(__name__)

SLUG = chatbot.WORKSHOP_SLUG

SESSION_NOT_FOUND_FA = "گفتگوی مورد نظر یافت نشد."
MESSAGE_NOT_FOUND_FA = "پیام مورد نظر یافت نشد."
ASK_ALREADY_RUNNING_FA = "پاسخ این پرسش همین حالا در حال تولید است."
# پیام‌های فارسی خطا از خودِ سرویس می‌آیند تا متن پاسخ HTTP و متن ذخیره‌شده روی
# ردیف پیام (که UI در حالت ``failed`` نشان می‌دهد) همیشه یکی باشند.
ASK_FAILED_FA = chatbot.ASK_FAILED_FA

router = APIRouter(prefix=f"/api/projects/{{project_id}}/{SLUG}", tags=[SLUG])

_CONFIDENCE_VALUES = ("high", "medium", "needs_review")


# ---------------------------------------------------------------------------
# صفحه‌ی HTML کارگاه (داخل یک پروژه)
# ---------------------------------------------------------------------------
def render_page(request: Request, project: Project, user: User) -> HTMLResponse:
    """صفحه‌ی چت -- با پرچم «در دسترس بودن» برای حالت دروازه‌بندی‌شده.

    فهرست گفتگوها عمداً این‌جا (سرور-ساید) خوانده نمی‌شود: صفحه بدون هیچ گفتگویی
    رندر می‌شود و خودِ فایل JS، فهرست را از ``GET /sessions`` و پیام‌های هر گفتگو
    را از ``GET /sessions/{id}/messages`` می‌گیرد. مزیت عملی این است که همین یک
    مسیر، هم بارگذاری اولیه و هم بعد از ساخت/حذف گفتگو را پوشش می‌دهد و صفحه‌ی
    HTML هرگز داده‌ی قدیمی نشان نمی‌دهد.
    """
    return templates.TemplateResponse(
        request,
        WORKSHOP.page_template,
        workshop_page_context(
            project,
            user,
            WORKSHOP,
            chatbot_available=chatbot.is_available(project),
            gate_message=chatbot.GATED_MESSAGE_FA,
            # پیوند «برو به کارگاه چک‌لیست» از خود رجیستری ساخته می‌شود (نه یک مسیر
            # هاردکد در قالب) تا اگر روزی اسلاگ/مسیر آن کارگاه عوض شد، این پیوند هم
            # خودکار دنبالش بیاید. اگر به هر دلیلی پیدا نشد، به صفحه‌ی پروژه برمی‌گردیم.
            checklist_url=_checklist_page_url(project),
        ),
    )


def _checklist_page_url(project: Project) -> str:
    checklist = get_workshop("checklist")
    if checklist is None:  # pragma: no cover -- رجیستری همیشه آن را دارد
        return f"/projects/{project.id}"
    return checklist.page_url(project.id)


# ---------------------------------------------------------------------------
# API: گفتگوها
# ---------------------------------------------------------------------------
def _session_out(chat) -> ChatSessionOut:
    return ChatSessionOut(
        id=chat.id,
        title=chat.title,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
    )


def _sources_out(row) -> list[ChatSourceOut]:
    """منابع استناد یک پیام را اعتبارسنجی می‌کند و منبع خراب را دور می‌ریزد.

    ``sources`` روی ردیف یک متن JSON است؛ اگر روزی شکلی غیرمنتظره داشته باشد،
    نمایش تاریخچه نباید با یک ۵۰۰ بشکند -- همان سیاست «بهترین‌تلاش»ی که
    ``runs_for_history`` برای برچسب‌های تاریخچه دارد.
    """
    sources: list[ChatSourceOut] = []
    for item in messages_repo.parsed_sources(row):
        try:
            sources.append(ChatSourceOut.model_validate(item))
        except ValidationError:
            logger.warning("dropping malformed source of chat message %s", row.id)
    return sources


def _message_out(row) -> ChatMessageOut:
    return ChatMessageOut(
        id=row.id,
        session_id=row.session_id,
        role=row.role,
        content=row.content or "",
        status=row.status,
        confidence=row.confidence if row.confidence in _CONFIDENCE_VALUES else None,
        sources=_sources_out(row),
        route_reasoning=row.route_reasoning or "",
        error_message=row.error_message,
        created_at=row.created_at,
    )


@router.post("/sessions", response_model=ChatSessionOut, status_code=201)
async def create_chat_session(
    payload: Optional[CreateChatSessionRequest] = None,
    project: Project = Depends(require_api_project),
    session: Session = Depends(get_session),
) -> ChatSessionOut:
    """ساخت یک گفتگوی جدید (خالی؛ پیام‌ها با هر پرسش اضافه می‌شوند)."""
    chat = sessions_repo.create(
        session,
        project_id=project.id,
        user_id=project.user_id,
        title=(payload.title if payload is not None else None),
    )
    logger.info("chat session %s created (project=%s)", chat.id, project.id)
    return _session_out(chat)


@router.get("/sessions", response_model=ChatSessionListOut)
async def list_chat_sessions(
    project: Project = Depends(require_api_project),
    session: Session = Depends(get_session),
) -> ChatSessionListOut:
    """گفتگوهای همین پروژه برای همین کاربر (جدیدترین استفاده‌شده اول).

    گفتگوی هیچ کاربر/پروژه‌ی دیگری هرگز در این فهرست نمی‌آید -- پرس‌وجو هر دو
    شناسه را فیلتر می‌کند (نه فقط یکی).
    """
    chats = sessions_repo.list_by_project(session, project.id, user_id=project.user_id)
    return ChatSessionListOut(sessions=[_session_out(chat) for chat in chats])


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_chat_session(
    session_id: str,
    project: Project = Depends(require_api_project),
    session: Session = Depends(get_session),
) -> Response:
    """حذف یک گفتگو **همراه با کل تاریخچه‌ی پیام‌هایش**.

    ترتیب عمدی است: ابتدا پیام‌های همان گفتگو (با همان کلید سه‌گانه‌ی
    ``session_id``/``project_id``/``user_id``) و سپس ردیف گفتگو. با اینکه
    ``ON DELETE CASCADE`` هم وجود دارد، حذف صریح تضمین می‌کند هیچ ردیف یتیمی
    باقی نمی‌ماند -- دقیقاً همان سیاستی که حذف پایگاه‌دانش/فایل‌هایش دارد.

    گفتگویی که متعلق به این پروژه/کاربر نباشد، «پیدا نشد» (۴۰۴) می‌دهد -- نه
    اینکه با شناسه‌ی حدسی حذف شود.
    """
    chat = sessions_repo.get_by_id(
        session, session_id, user_id=project.user_id, project_id=project.id
    )
    if chat is None:
        raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND_FA)

    removed = messages_repo.delete_all_chat_messages_for_session(
        session, session_id, project.id, project.user_id
    )
    sessions_repo.delete_by_id(
        session, session_id, user_id=project.user_id, project_id=project.id
    )
    logger.info("chat session %s deleted (project=%s, messages=%d)", session_id, project.id, removed)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# API: پیام‌های یک گفتگو (تاریخچه + polling وضعیت پاسخ)
# ---------------------------------------------------------------------------
@router.get("/sessions/{session_id}/messages", response_model=ChatMessageListOut)
async def list_session_messages(
    session_id: str,
    project: Project = Depends(require_api_project),
    session: Session = Depends(get_session),
) -> ChatMessageListOut:
    """تاریخچه‌ی کامل یک گفتگو، قدیمی‌ترین اول -- همان چیزی که باز کردن گفتگو را می‌سازد.

    اگر گفتگو متعلق به این پروژه/کاربر نباشد، ۴۰۴ می‌دهد (نه یک فهرست خالی):
    «وجود ندارد» و «خالی است» دو چیز متفاوت‌اند و UI باید بتواند تشخیص دهد.
    """
    chat = sessions_repo.get_by_id(
        session, session_id, user_id=project.user_id, project_id=project.id
    )
    if chat is None:
        raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND_FA)

    rows = messages_repo.list_by_session(
        session, session_id, project_id=project.id, user_id=project.user_id
    )
    return ChatMessageListOut(messages=[_message_out(row) for row in rows])


@router.get("/sessions/{session_id}/messages/{message_id}", response_model=ChatMessageOut)
async def get_session_message(
    session_id: str,
    message_id: str,
    project: Project = Depends(require_api_project),
    session: Session = Depends(get_session),
) -> ChatMessageOut:
    """وضعیت/محتوای *یک* پیام -- همان endpoint ی که UI پس از هر پرسش polling می‌کند.

    بررسی جداسازی سه‌گانه است (``session_id``/``project_id``/``user_id``) و در همان
    یک پرس‌وجوی تک‌جدولی انجام می‌شود، پس یک شناسه‌ی حدسی از مرز پروژه/کاربر/گفتگو
    عبور نمی‌کند (۴۰۴، نه ۴۰۳).
    """
    chat = sessions_repo.get_by_id(
        session, session_id, user_id=project.user_id, project_id=project.id
    )
    if chat is None:
        raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND_FA)

    row = messages_repo.get_by_id(
        session,
        message_id,
        session_id=session_id,
        project_id=project.id,
        user_id=project.user_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail=MESSAGE_NOT_FOUND_FA)
    return _message_out(row)


# ---------------------------------------------------------------------------
# API: پرسش (ثبت فوری، پاسخ در پس‌زمینه)
# ---------------------------------------------------------------------------
@router.post("/sessions/{session_id}/ask", response_model=ChatAskStartedOut, status_code=202)
def ask_in_session(
    session_id: str,
    payload: ChatAskRequest,
    project: Project = Depends(require_api_project),
    session: Session = Depends(get_session),
) -> ChatAskStartedOut:
    """پرسش از پایگاه‌دانش پروژه در زمینه‌ی همین گفتگو -- و بازگشت فوری.

    ترتیب کار عمدی است:

    ۱) گفتگو باید متعلق به همین ``(project_id, user_id)`` باشد (وگرنه ۴۰۴)،
    ۲) پروژه باید پایگاه‌دانش آماده داشته باشد (وگرنه ۴۰۹ با پیام فارسی راهنما --
       این تنها بررسی *ارزان* است و تنها چیزی است که پیش از ثبت پرسش لازم است؛
       ساختن پایگاه‌دانش فقط-خواندنی یک عملیات سنگین است و در پس‌زمینه انجام
       می‌شود)،
    ۳) پرسش کاربر ذخیره می‌شود (همین ردیف است که بلافاصله در UI رندر می‌شود و
       پس از refresh هم برمی‌گردد -- رفع باگ «پیام کاربر نمایش داده نمی‌شد»
       سمت سرور از همین‌جا می‌آید)،
    ۴) ردیف جانشین پاسخ با ``status="pending"`` ساخته می‌شود تا UI شناسه‌ای برای
       polling داشته باشد،
    ۵) کار واقعی در یک ترد پس‌زمینه به ``chat_ask_jobs`` سپرده می‌شود و پاسخ HTTP
       **منتظر مدل نمی‌ماند**.

    کلید job دقیقاً ``(project_id, session_id, assistant_message_id)`` است، پس دو
    پرسش هم‌زمان در دو گفتگو (یا دو پروژه) هرکدام کار مستقل خودشان را دارند و هیچ
    حالت مشترکی بین‌شان نیست.

    تابع عمداً ``def`` است (نه ``async def``): دو درج پایگاه‌داده‌ای در threadpool
    اجرا می‌شوند و حلقه‌ی رویداد آزاد می‌ماند (``api/routers/settings.py::
    test_workshop_connection`` هم به همین دلیل ``def`` است).
    """
    chat = sessions_repo.get_by_id(
        session, session_id, user_id=project.user_id, project_id=project.id
    )
    if chat is None:
        raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND_FA)

    if not chatbot.is_available(project):
        raise HTTPException(status_code=409, detail=chatbot.GATED_MESSAGE_FA)

    question = (payload.question or "").strip()
    if not question:
        raise HTTPException(status_code=422, detail=chatbot.EMPTY_QUESTION_FA)

    user_message = messages_repo.create_user_message(
        session,
        session_id=chat.id,
        project_id=project.id,
        user_id=project.user_id,
        content=question,
    )
    assistant_message = messages_repo.create_pending_assistant_message(
        session,
        session_id=chat.id,
        project_id=project.id,
        user_id=project.user_id,
    )

    started = chat_ask_jobs.submit(
        (project.id, chat.id, assistant_message.id),
        partial(
            chatbot.answer_question,
            user_id=project.user_id,
            project_id=project.id,
            session_id=chat.id,
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
        ),
    )
    if not started:  # pragma: no cover -- با UUID تازه عملاً رخ نمی‌دهد
        # ردیف ``pending`` نباید بی‌صاحب بماند؛ همان‌جا ناموفق علامت می‌خورد.
        messages_repo.mark_assistant_message_failed(
            session, assistant_message.id, ASK_ALREADY_RUNNING_FA
        )
        raise HTTPException(status_code=409, detail=ASK_ALREADY_RUNNING_FA)

    return ChatAskStartedOut(
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        status="pending",
    )


# ---------------------------------------------------------------------------
# callable های اجباری رجیستری -- این کارگاه ردیف اجرا ندارد، پس بی‌اثر و امن‌اند
# ---------------------------------------------------------------------------
def estimate_progress(job: dict[str, Any]) -> int:
    """هیچ job ای در `JobManager` وجود ندارد؛ این تابع فقط قرارداد رجیستری را برآورده می‌کند."""
    return 0


def collect_result(job: dict[str, Any]) -> ResultArtifact:
    """هیچ نتیجه‌ی فایلی برای ذخیره وجود ندارد (پاسخ‌ها در جدول پیام‌ها می‌مانند)."""
    return ResultArtifact(summary={})


def has_download(job: dict[str, Any]) -> bool:
    """این کارگاه همیشه بدون فایل خروجی است."""
    return False


def summary_chips_fa(summary: dict[str, Any]) -> list[str]:
    """برچسبی برای تاریخچه وجود ندارد (این کارگاه در تاریخچه‌ی اجراها نیست)."""
    return []


# ---------------------------------------------------------------------------
# ثبت در رجیستری کارگاه‌ها
# ---------------------------------------------------------------------------
WORKSHOP = register(
    WorkshopDefinition(
        slug=SLUG,
        display_name_fa="چت‌بات مالی",
        description_fa=(
            "پرسش و پاسخ با اسناد حسابرسی پروژه بر پایه‌ی پایگاه‌دانش ساخته‌شده "
            "توسط کارگاه چک‌لیست؛ با گفتگوهای نام‌دار و تاریخچه‌ی ماندگار، پاسخ‌های "
            "قالب‌بندی‌شده به‌همراه منابع استناد، و تولید پاسخ در پس‌زمینه (می‌توانید "
            "در حین پاسخ‌دهی به کارگاه دیگری بروید). برای فعال شدن، ابتدا یک تحلیل "
            "در کارگاه چک‌لیست لازم است."
        ),
        icon="sparkles",
        accent="gold",
        badge_fa="پرسش و پاسخ",
        router=router,
        page_template="financial_chatbot.html",
        page_renderer=render_page,
        estimate_progress=estimate_progress,
        collect_result=collect_result,
        has_download=has_download,
        summary_chips_fa=summary_chips_fa,
        highlights_fa=(
            "پاسخ بر پایه‌ی جدیدترین پایگاه‌دانش آماده‌ی پروژه (نتیجه‌ی کارگاه چک‌لیست)",
            "گفتگوهای نام‌دار با تاریخچه‌ی کامل پیام‌ها -- باز کردن هر گفتگو، پرسش‌ها و پاسخ‌های قبلی‌اش را نشان می‌دهد",
            "پاسخ‌دهی در پس‌زمینه: می‌توانید به کارگاه یا گفتگوی دیگری بروید و بعد نتیجه را ببینید",
            "پاسخ‌های Markdown با استناد به فایل و شیت منبع، در رابط راست‌به‌چپ",
        ),
        settings_keys=("api_key", "api_url", "model", "temperature", "max_output_tokens"),
        # تنظیمات اختصاصی این کارگاه واقعاً در زمان هر پرسش اعمال می‌شود (نگاه کنید به
        # ``rag_ai_adapter.build_llm_client`` با همان slug همین کارگاه)، و دکمه‌ی
        # «تست اتصال» فرم از همان کلاینت استفاده می‌کند.
        settings_applied=True,
        test_connection=chatbot.test_connection,
    )
)
