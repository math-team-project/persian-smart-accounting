"""کارگاه «چت‌بات مالی» به‌عنوان یک ورودی رجیستری.

ساختار این ماژول از ``api/workshops/budget_analysis.py`` پیروی می‌کند (همان الگوی
``router`` + ``render_page`` + ``register(WorkshopDefinition(...))``)، اما **این
کارگاه یک کارگاه job/پس‌زمینه نیست**: چت یک درخواست/پاسخ زنده است، نه یک پردازش
چنددقیقه‌ای. بنابراین:

* هیچ ``workshop_runs``ی ساخته نمی‌شود و هیچ job ای در ``JobManager`` ثبت نمی‌شود
  (پس در پنل «کارهای در جریان» و تاریخچه‌ی پروژه ظاهر نمی‌شود -- و این درست است:
  تاریخچه‌ی این کارگاه *گفتگوها* هستند، نه اجراها).
* چهار callable اجباری رجیستری (``estimate_progress``/``collect_result``/
  ``has_download``/``summary_chips_fa``) با پیاده‌سازی‌های حداقلی و بی‌اثر پر
  شده‌اند، چون این کارگاه هیچ job ای تولید نمی‌کند که آن‌ها روی آن صدا زده شوند.
* مدیریت گفتگوها (ساخت/فهرست/حذف) در ``api/repositories/chat_sessions.py`` است و
  منطق پردازش/پرسش در ``api/services/financial_chatbot_service.py``.
* چون این کارگاه واقعاً تنظیمات هوش مصنوعی پروژه را در زمان اجرا اعمال می‌کند
  (``rag_ai_adapter.build_llm_client``)، ``settings_applied=True`` و یک
  ``test_connection`` دارد؛ بنابراین فرم تنظیمات آن به‌صورت خودکار در صفحه‌ی پروژه
  ظاهر می‌شود (``workshops_with_settings()``) بدون هیچ تغییری در قالب‌ها.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from api.auth.deps import require_api_project
from api.db.base import get_session
from api.db.models import Project, User
from api.repositories import chat_sessions as sessions_repo
from api.schemas.chatbot import (
    ChatAskOut,
    ChatAskRequest,
    ChatSessionListOut,
    ChatSessionOut,
    CreateChatSessionRequest,
)
from api.services import financial_chatbot_service as chatbot
from api.templating import templates
from api.workshops.pages import workshop_page_context
from api.workshops.registry import ResultArtifact, WorkshopDefinition, get as get_workshop, register

logger = logging.getLogger(__name__)

SLUG = chatbot.WORKSHOP_SLUG

SESSION_NOT_FOUND_FA = "گفتگوی مورد نظر یافت نشد."
ASK_FAILED_FA = "خطای غیرمنتظره‌ای هنگام پاسخ‌دهی رخ داد. لطفاً دوباره تلاش کنید."

router = APIRouter(prefix=f"/api/projects/{{project_id}}/{SLUG}", tags=[SLUG])


# ---------------------------------------------------------------------------
# صفحه‌ی HTML کارگاه (داخل یک پروژه)
# ---------------------------------------------------------------------------
def render_page(request: Request, project: Project, user: User) -> HTMLResponse:
    """صفحه‌ی چت -- با پرچم «در دسترس بودن» برای حالت دروازه‌بندی‌شده.

    فهرست گفتگوها عمداً این‌جا (سرور-ساید) خوانده نمی‌شود: صفحه بدون هیچ گفتگویی
    رندر می‌شود و خودِ فایل JS، فهرست را از ``GET /sessions`` می‌گیرد. مزیت عملی
    این است که همین یک مسیر، هم بارگذاری اولیه و هم بعد از ساخت/حذف گفتگو را
    پوشش می‌دهد و صفحه‌ی HTML هرگز داده‌ی قدیمی نشان نمی‌دهد.
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
            max_history_turns=chatbot.MAX_CONTEXT_TURNS,
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


@router.post("/sessions", response_model=ChatSessionOut, status_code=201)
async def create_chat_session(
    payload: Optional[CreateChatSessionRequest] = None,
    project: Project = Depends(require_api_project),
    session: Session = Depends(get_session),
) -> ChatSessionOut:
    """ساخت یک گفتگوی جدید (فقط متادیتا: عنوان). هیچ پیامی ذخیره نمی‌شود."""
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
    session_id: int,
    project: Project = Depends(require_api_project),
    session: Session = Depends(get_session),
) -> Response:
    """حذف یک گفتگو -- حذف سخت همین یک ردیف.

    چون هیچ پیامی (نه در پایگاه‌داده، نه روی دیسک) ذخیره نمی‌شود، چیز دیگری برای
    پاک‌کردن وجود ندارد. گفتگویی که متعلق به این پروژه/کاربر نباشد، «پیدا نشد»
    (۴۰۴) می‌دهد -- نه اینکه با شناسه‌ی حدسی حذف شود.
    """
    deleted = sessions_repo.delete_by_id(
        session, session_id, user_id=project.user_id, project_id=project.id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND_FA)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# API: پرسش
# ---------------------------------------------------------------------------
@router.post("/sessions/{session_id}/ask", response_model=ChatAskOut)
async def ask_in_session(
    session_id: int,
    payload: ChatAskRequest,
    project: Project = Depends(require_api_project),
    session: Session = Depends(get_session),
) -> ChatAskOut:
    """پرسش از پایگاه‌دانش پروژه، در زمینه‌ی همین گفتگو.

    نکته‌ی مهم (بی‌حالتی سرور): ``recent_history`` فقط از همین بدنه‌ی درخواست
    خوانده می‌شود. سرور هیچ حالتی از گفتگو نگه نمی‌دارد، بنابراین دو پرسش هم‌زمان
    روی دو گفتگوی مختلف پروژه، هرکدام فقط زمینه‌ی خودش را «می‌بیند» و پاسخ‌ها
    هرگز با هم قاطی نمی‌شوند. تاریخچه نه خوانده و نه نوشته می‌شود.
    """
    chat = sessions_repo.get_by_id(
        session, session_id, user_id=project.user_id, project_id=project.id
    )
    if chat is None:
        raise HTTPException(status_code=404, detail=SESSION_NOT_FOUND_FA)

    try:
        result = chatbot.ask_with_history(
            session, project, payload.question, payload.recent_history
        )
    except chatbot.ChatbotUnavailableError as exc:
        # وضعیت پروژه اجازه‌ی پرسش نمی‌دهد (بدون پایگاه‌دانش آماده / بدون متن
        # قابل‌جست‌وجو) -- پیام فارسی خودِ سرویس عیناً به UI می‌رود.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 -- هیچ stack trace خامی به کاربر نرسد
        logger.exception("financial chatbot ask failed (project=%s)", project.id)
        raise HTTPException(status_code=500, detail=ASK_FAILED_FA) from exc

    # فقط پس از یک پاسخ موفق «آخرین استفاده» گفتگو تازه می‌شود (تا فهرست کاربر با
    # گفتگویی که پرسشش شکست خورده بالا نیاید).
    sessions_repo.touch(session, chat)

    return ChatAskOut(
        answer=result.get("answer", ""),
        confidence=result.get("confidence", "medium"),
        sources=result.get("sources", []),
        route_reasoning=result.get("route_reasoning", ""),
        history_turns_used=int(result.get("history_turns_used", 0) or 0),
    )


# ---------------------------------------------------------------------------
# callable های اجباری رجیستری -- این کارگاه job ندارد، پس بی‌اثر و امن‌اند
# ---------------------------------------------------------------------------
def estimate_progress(job: dict[str, Any]) -> int:
    """هیچ job ای وجود ندارد؛ این تابع فقط قرارداد رجیستری را برآورده می‌کند."""
    return 0


def collect_result(job: dict[str, Any]) -> ResultArtifact:
    """هیچ نتیجه‌ی فایلی برای ذخیره وجود ندارد (پاسخ‌ها ذخیره نمی‌شوند)."""
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
            "پرسش و پاسخ زنده با اسناد حسابرسی پروژه بر پایه‌ی پایگاه‌دانش ساخته‌شده "
            "توسط کارگاه چک‌لیست؛ با امکان ساخت، فهرست و حذف گفتگوها و پاسخ‌های "
            "قالب‌بندی‌شده به‌همراه منابع استناد. برای فعال شدن، ابتدا یک تحلیل در "
            "کارگاه چک‌لیست لازم است."
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
            "گفتگوهای نام‌دار و قابل حذف -- فقط متادیتا ذخیره می‌شود، بدون هیچ متن پیامی",
            "زمینه‌ی چندنوبتی در همان گفتگوی باز (فقط در حافظه‌ی مرورگر، هرگز روی سرور)",
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
