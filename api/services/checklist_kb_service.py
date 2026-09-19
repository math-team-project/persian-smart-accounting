"""
ارکستراسیون چرخه‌ی عمر پایگاه‌دانش (Knowledge Base) کارگاه چک‌لیست.

این ماژول اولین جایی است که ``rag_chat_module`` واقعاً به یک کارگاه وصل
می‌شود: با هر بار «انجام تحلیل» در کارگاه چک‌لیست:

  ۱) یک ردیف ``knowledge_bases`` جدید (``status="indexing"``) ساخته می‌شود و
     همان لحظه، در یک ترد پس‌زمینه‌ی مستقل، ایندکس‌سازی فایل‌های آپلودی +
     خودِ فایل تعریف چک‌لیست شروع می‌شود -- بدون اینکه اجرای چک‌لیست موجود
     (``pipeline.run_full_pipeline``) کندتر یا منتظر بماند.
  ۲) وقتی پایپلاین چک‌لیست به نتیجه‌ی خام (موارد FALSE) می‌رسد، پیش از تولید
     گزارش کمیسیون، یک نقطه‌ی «join» ساده (``ChecklistKBJoin``) صبر می‌کند تا
     ایندکس‌سازی هم تمام شود؛ اگر موفق بود، مرحله‌ی «تطبیق هوشمند»
     (``rag_chat_module.llm_variable_resolver.full_run.run_full_audit``) روی
     همان پایگاه‌دانش تازه‌ساخته اجرا می‌شود و نتیجه‌ی اصلاح‌شده جایگزین موارد
     خام FALSE در گزارش کمیسیون می‌شود.
  ۳) نتیجه‌ی تطبیق‌شده به‌عنوان یک فایل Markdown به همان پایگاه‌دانش اضافه
     می‌شود (تا چت‌بات آینده بتواند «سوال ۲۰ چه چیزی را بررسی می‌کند؟» یا
     «کدام موارد چک‌لیست رد شدند؟» را پاسخ دهد)، سپس پایگاه‌دانش ``ready``
     می‌شود و ``projects.latest_ready_kb_id`` به‌روزرسانی می‌شود.
  ۴) بلافاصله پس از آن، سیاست نگهداری (``PSA_KB_RETENTION_COUNT``) اعمال
     می‌شود: پایگاه‌دانش‌های قدیمی‌تر همان پروژه (به‌جز جدیدترینِ ``ready``) هم
     از دیسک و هم از پایگاه‌داده حذف می‌شوند.

هر خطا در هر مرحله باید به‌صورت graceful مدیریت شود: مشکل پایگاه‌دانش هرگز
نباید کارگاه چک‌لیست را (که پیش از این فاز کاملاً کار می‌کرد) خراب کند --
در بدترین حالت، گزارش کمیسیون از موارد خام (بدون تطبیق هوشمند) ساخته می‌شود.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable, Optional

import pipeline
from api.config import get_settings
from api.db.base import SessionLocal
from api.db.models import Project, new_kb_id
from api.repositories import knowledge_bases as kb_repo
from api.services import kb_storage, rag_ai_adapter

logger = logging.getLogger(__name__)

CHECKLIST_DEFINITION_KEY = "checklist_definition"
CHECKLIST_RESULTS_KEY = "checklist_results"
CHECKLIST_WORKSHOP_SLUG = "checklist"

# توضیح کوتاه و مبتنی‌بر برچسب‌های واقعی هر اسلات آپلود (``pipeline.FILE_SLOTS``)
# -- عمداً چیزی فراتر از این ادعا نمی‌شود که برای این فاز کافی است.
_FILE_SLOT_DESCRIPTIONS: dict[str, dict[str, str]] = {
    slot_key: {"overall_description": f"{slot['label']} -- {slot['help']}"}
    for slot_key, slot in pipeline.FILE_SLOTS.items()
    if slot_key != "audit_report_doc"
}

_CHECKLIST_DEFINITION_DESCRIPTION = {
    "overall_description": (
        "بانک سوالات چک‌لیست حسابرسی مالی (Checklist_Question_extracted_handler.json)"
        " -- هر سوال شامل شرح، هدف و شرط ارزیابی است."
    )
}


def _build_sheet_descriptions() -> dict[str, dict[str, str]]:
    descriptions = dict(_FILE_SLOT_DESCRIPTIONS)
    descriptions[CHECKLIST_DEFINITION_KEY] = _CHECKLIST_DEFINITION_DESCRIPTION
    return descriptions


def sheet_descriptions() -> dict[str, dict[str, str]]:
    """توصیف هر فایل (به تفکیک کلید logical) -- همان متنی که هنگام ایندکس‌سازی
    به ``metadata_text`` هر chunk اضافه می‌شود.

    عمومی و بدون پایگاه‌داده، چون مصرف‌کننده‌ی دیگری هم دارد: کارگاه چت‌بات مالی
    همین توصیف‌ها را به‌عنوان «کاتالوگ فایل‌ها» به روتر مدل می‌دهد (نگاه کنید به
    ``api/services/financial_chatbot_service.py::build_sheet_catalog``). این تابع
    قطعی است (فقط از ``pipeline.FILE_SLOTS`` ساخته می‌شود) و هیچ حالت/پایه‌داده‌ای
    ندارد، بنابراین نیازی به ذخیره‌کردن جداگانه‌اش در پایگاه‌دانش نیست.
    """
    return _build_sheet_descriptions()


def _build_file_registry(file_paths: dict[str, Optional[Path]]) -> dict[str, str]:
    """فقط اسلات‌هایی که واقعاً آپلود شده‌اند + خودِ فایل تعریف چک‌لیست."""
    registry: dict[str, str] = {}
    for slot_key, path in file_paths.items():
        if path is None or slot_key == "audit_report_doc":
            continue
        registry[slot_key] = str(path)
    registry[CHECKLIST_DEFINITION_KEY] = str(pipeline.CHECKLIST_HANDLER_PATH)
    return registry


# ---------------------------------------------------------------------------
# نقطه‌ی «join» بین ترد ایندکس‌سازی و ترد پایپلاین چک‌لیست
# ---------------------------------------------------------------------------
class ChecklistKBJoin:
    """هماهنگ‌کننده‌ی سبک بین ترد ایندکس‌سازی و ترد پایپلاین چک‌لیست.

    سبک این کلاس دقیقاً مطابق الگوی هماهنگی موجود در این پروژه است (یک
    دیکشنری/شیء مشترک بین تردها + ``threading.Event``) -- بدون افزودن هیچ
    ابزار همزمانی جدیدی.  ترد ایندکس‌سازی وقتی کارش (موفق یا ناموفق) تمام شد
    ``mark_indexing_finished`` را صدا می‌زند؛ ترد پایپلاین چک‌لیست پیش از
    تولید گزارش کمیسیون، ``resolve_false_questions`` را صدا می‌زند که تا
    پایان ایندکس‌سازی مسدود می‌ماند.
    """

    def __init__(
        self,
        *,
        user_id: int,
        project_id: int,
        file_registry: dict[str, str],
        sheet_descriptions: dict[str, dict[str, str]],
    ) -> None:
        self.user_id = user_id
        self.project_id = project_id
        self.file_registry = file_registry
        self.sheet_descriptions = sheet_descriptions
        self.kb_id: Optional[str] = None
        self._indexing_done = threading.Event()
        self._indexing_ok = False

    def mark_indexing_finished(self, *, ok: bool, kb_id: Optional[str]) -> None:
        self.kb_id = kb_id
        self._indexing_ok = ok
        self._indexing_done.set()

    def resolve_false_questions(
        self,
        false_questions: list[dict[str, Any]],
        checklist_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """نقطه‌ی join -- در ترد پایپلاین چک‌لیست فراخوانی می‌شود."""
        self._indexing_done.wait()
        if not self._indexing_ok or not self.kb_id:
            logger.warning(
                "checklist KB indexing was not successful; falling back to raw checklist results"
            )
            return false_questions

        try:
            return _run_resolver_and_feed_back(
                kb_id=self.kb_id,
                user_id=self.user_id,
                project_id=self.project_id,
                file_registry=self.file_registry,
                sheet_descriptions=self.sheet_descriptions,
                checklist_results=checklist_results,
                false_questions=false_questions,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "checklist KB resolver step failed for kb=%s; falling back to raw checklist results",
                self.kb_id,
            )
            _mark_kb_ready_best_effort(self.kb_id, self.project_id)
            return false_questions


# ---------------------------------------------------------------------------
# مرحله‌ی ۱: شروع ایندکس‌سازی (ترد جدا، بدون انتظار)
# ---------------------------------------------------------------------------
def start_indexing(
    *,
    user_id: int,
    project_id: int,
    file_paths: dict[str, Optional[Path]],
) -> ChecklistKBJoin:
    """یک ردیف پایگاه‌دانش جدید می‌سازد و ایندکس‌سازی را در ترد پس‌زمینه شروع می‌کند.

    این تابع فوراً برمی‌گردد (خودِ ایندکس‌سازی در یک ترد جدا انجام می‌شود) --
    دقیقاً همان الگوی ``pipeline.start_checklist_job``.
    """
    file_registry = _build_file_registry(file_paths)
    sheet_descriptions = _build_sheet_descriptions()

    join = ChecklistKBJoin(
        user_id=user_id,
        project_id=project_id,
        file_registry=file_registry,
        sheet_descriptions=sheet_descriptions,
    )

    def _worker() -> None:
        kb_id: Optional[str] = None
        session = SessionLocal()
        try:
            embedder, device = rag_ai_adapter.build_embedder()
            settings = get_settings()
            kb_id = new_kb_id()
        except Exception:  # noqa: BLE001
            logger.exception("checklist KB indexing: failed before creating KB row")
            session.close()
            join.mark_indexing_finished(ok=False, kb_id=None)
            return

        try:
            # factory «ضبط‌کننده» به‌جای factory ساده: علاوه بر Chroma، متن کامل هر
            # chunk را هم در ``chunks.jsonl`` همین پایگاه‌دانش می‌نویسد. بدون آن،
            # چت‌بات در یک فرایند تازه هیچ چیزی برای جست‌وجو ندارد (نگاه کنید به
            # ``api/services/kb_storage.py``). این کار ایندکس‌سازی را کند نمی‌کند --
            # فقط یک نوشتن خطی در کنار همان افزودن به Chroma است.
            vector_store_factory = rag_ai_adapter.build_recording_vector_store_factory(
                user_id, project_id, kb_id
            )
            kb = kb_repo.create(
                session,
                project_id=project_id,
                user_id=user_id,
                embedding_model=settings.embedding_model,
                embedding_device=device,
                chroma_collection_name=rag_ai_adapter.chroma_collection_name_for(kb_id),
                chroma_persist_dir=kb_storage.relative_path_for(user_id, project_id, kb_id),
                kb_id=kb_id,
            )
            kb_storage.write_manifest(
                user_id,
                project_id,
                kb_id,
                embedding_model=settings.embedding_model,
                embedding_device=device,
                chroma_collection_name=kb.chroma_collection_name,
                created_at=kb.created_at.isoformat(),
            )

            from llm_variable_resolver.knowledge_base import KnowledgeBase

            knowledge_base = KnowledgeBase(
                embedder=embedder, vector_store_factory=vector_store_factory
            )
            report = knowledge_base.index_files(file_registry, sheet_descriptions=sheet_descriptions)

            for file_key, info in report.files_indexed.items():
                kb_repo.add_file(
                    session,
                    kb.id,
                    file_key=file_key,
                    original_filename=Path(file_registry[file_key]).name,
                    status=info.get("status", "error"),
                    sheet_count=int(info.get("sheets", 0) or 0),
                    chunk_count=int(info.get("chunks", 0) or 0),
                    error=info.get("error"),
                )

            if report.total_chunks <= 0:
                message = "؛ ".join(report.errors) or "هیچ محتوایی برای ایندکس‌سازی یافت نشد."
                kb_repo.update_status(session, kb.id, status=kb_repo.FAILED, error_message=message)
                logger.warning("checklist KB indexing produced no chunks (kb=%s): %s", kb.id, message)
                join.mark_indexing_finished(ok=False, kb_id=kb.id)
                return

            logger.info(
                "checklist KB indexing finished (kb=%s, files=%s, chunks=%s)",
                kb.id,
                report.total_files,
                report.total_chunks,
            )
            join.mark_indexing_finished(ok=True, kb_id=kb.id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("checklist KB indexing failed (kb=%s)", kb_id)
            if kb_id is not None:
                try:
                    kb_repo.update_status(
                        session, kb_id, status=kb_repo.FAILED, error_message=str(exc)
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("failed to persist KB failure status for kb=%s", kb_id)
            join.mark_indexing_finished(ok=False, kb_id=kb_id)
        finally:
            session.close()

    threading.Thread(
        target=_worker, name="checklist-kb-indexing", daemon=True
    ).start()
    return join


# ---------------------------------------------------------------------------
# مرحله‌ی ۳ و ۴: تطبیق هوشمند (resolver) + بازخورد نتایج + آماده‌سازی نهایی
# ---------------------------------------------------------------------------
def _run_resolver_and_feed_back(
    *,
    kb_id: str,
    user_id: int,
    project_id: int,
    file_registry: dict[str, str],
    sheet_descriptions: dict[str, dict[str, str]],
    checklist_results: list[dict[str, Any]],
    false_questions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    from llm_variable_resolver.full_run import run_full_audit
    from llm_variable_resolver.llm.extractor import EnsembleExtractor, Voter
    from llm_variable_resolver.llm.orchestrator import RedesignEnsemble, RedesignVoter
    from llm_variable_resolver.retrieval import ChunkRetriever
    from llm_variable_resolver.size_router import SizeRouter

    session = SessionLocal()
    try:
        llm_client = rag_ai_adapter.build_llm_client(
            session, project_id, workshop_slug=CHECKLIST_WORKSHOP_SLUG
        )
    finally:
        session.close()

    embedder, _device = rag_ai_adapter.build_embedder()
    vector_store_factory = rag_ai_adapter.build_vector_store_factory(user_id, project_id, kb_id)

    extractor = EnsembleExtractor([Voter(name="checklist-resolver", client=llm_client)])
    router = SizeRouter(
        chunk_retriever=ChunkRetriever(
            embedder=embedder, vector_store_factory=vector_store_factory
        )
    )
    redesign_ensemble = RedesignEnsemble(
        [RedesignVoter(name="checklist-redesign", client=llm_client)]
    )

    checklist_definition = pipeline.load_checklist_definitions()

    report = run_full_audit(
        checklist=checklist_definition,
        operational=checklist_results,
        file_registry=file_registry,
        extractor=extractor,
        router=router,
        redesign_ensemble=redesign_ensemble,
        sheet_descriptions=sheet_descriptions,
    )

    updated_operational = report.updated_operational or checklist_results
    resolved_false_questions = [
        item for item in updated_operational if item.get("status") == "FALSE"
    ]

    try:
        _feed_checklist_results_into_kb(
            kb_id=kb_id,
            user_id=user_id,
            project_id=project_id,
            embedder=embedder,
            # مرحله‌ی «بازخورد نتایج» chunk های *تازه* (نتیجه‌ی تطبیق‌شده) را به همین
            # پایگاه‌دانش اضافه می‌کند، پس همان factory ضبط‌کننده لازم است تا متن
            # این فایل هم برای چت‌بات قابل جست‌وجو بماند. ``vector_store_factory``
            # بالای این تابع (مسیر جست‌وجوی resolver) عمداً ساده می‌ماند.
            vector_store_factory=rag_ai_adapter.build_recording_vector_store_factory(
                user_id, project_id, kb_id
            ),
            report_dict=report.to_dict(),
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "failed to feed checklist results back into KB %s; marking ready without them", kb_id
        )

    _mark_kb_ready_best_effort(kb_id, project_id)
    return resolved_false_questions


def _render_checklist_results_markdown(report_dict: dict[str, Any]) -> str:
    """گزارش تطبیق‌شده‌ی چک‌لیست را به یک سند Markdown ساده تبدیل می‌کند.

    ``MarkdownReader`` جداول Markdown زیر هر تیتر را به یک SheetData جدا
    تبدیل می‌کند -- همین رفتار برای این‌که چت‌بات آینده بتواند بین «خلاصه» و
    «موارد نامنطبق» تمایز قائل شود کافی است؛ نیازی به یک reader تازه نیست.
    """
    summary = report_dict.get("summary") or {}
    lines = ["# نتایج نهایی چک‌لیست حسابرسی (پس از تطبیق هوشمند)", "", "## خلاصه", ""]
    lines.append("| شاخص | مقدار |")
    lines.append("|---|---|")
    for key, value in summary.items():
        lines.append(f"| {key} | {value} |")

    lines.extend(["", "## موارد عدم تطابق (FALSE)", ""])
    lines.append("| question_id | question_text | message |")
    lines.append("|---|---|---|")
    for item in report_dict.get("updated_operational") or []:
        if item.get("status") != "FALSE":
            continue
        qid = str(item.get("question_id", "")).replace("|", "/")
        text = str(item.get("question_text", "")).replace("|", "/").replace("\n", " ")
        message = str(item.get("message", "")).replace("|", "/").replace("\n", " ")
        lines.append(f"| {qid} | {text} | {message} |")

    return "\n".join(lines) + "\n"


def _feed_checklist_results_into_kb(
    *,
    kb_id: str,
    user_id: int,
    project_id: int,
    embedder,
    vector_store_factory: Callable[[], object],
    report_dict: dict[str, Any],
) -> None:
    """نتیجه‌ی تطبیق‌شده‌ی چک‌لیست را به همان مجموعه‌ی Chroma این پایگاه‌دانش اضافه می‌کند."""
    import tempfile

    from llm_variable_resolver.knowledge_base import KnowledgeBase

    markdown = _render_checklist_results_markdown(report_dict)
    with tempfile.TemporaryDirectory(prefix="psa-kb-results-") as tmp:
        md_path = Path(tmp) / "checklist_results.md"
        md_path.write_text(markdown, encoding="utf-8")

        knowledge_base = KnowledgeBase(embedder=embedder, vector_store_factory=vector_store_factory)
        report = knowledge_base.index_files(
            {CHECKLIST_RESULTS_KEY: str(md_path)},
            sheet_descriptions={
                CHECKLIST_RESULTS_KEY: {
                    "overall_description": (
                        "نتایج نهایی ارزیابی چک‌لیست حسابرسی (پس از تطبیق هوشمند با اسناد) --"
                        " شامل خلاصه و فهرست موارد عدم تطابق."
                    )
                }
            },
        )

    session = SessionLocal()
    try:
        info = report.files_indexed.get(CHECKLIST_RESULTS_KEY, {})
        kb_repo.add_file(
            session,
            kb_id,
            file_key=CHECKLIST_RESULTS_KEY,
            original_filename="checklist_results.md",
            status=info.get("status", "error"),
            sheet_count=int(info.get("sheets", 0) or 0),
            chunk_count=int(info.get("chunks", 0) or 0),
            error=info.get("error"),
        )
    finally:
        session.close()


def _mark_kb_ready_best_effort(kb_id: Optional[str], project_id: int) -> None:
    """پایگاه‌دانش را ``ready`` می‌کند و بلافاصله سیاست نگهداری را اعمال می‌کند.

    این تابع هرگز استثنا پرتاب نمی‌کند -- بدترین حالت این است که پایگاه‌دانش
    در وضعیت ``indexing`` باقی می‌ماند (که با تلاش بعدی جایگزین می‌شود).
    """
    if kb_id is None:
        return
    session = SessionLocal()
    try:
        kb = kb_repo.mark_ready_and_update_project_pointer(session, kb_id)
        if kb is None:
            return
        _apply_retention(session, project_id)
    except Exception:  # noqa: BLE001
        logger.exception("failed to mark checklist KB %s as ready", kb_id)
    finally:
        session.close()


def _apply_retention(session, project_id: int) -> None:
    """پایگاه‌دانش‌های فراتر از ``PSA_KB_RETENTION_COUNT`` همین پروژه را پاک می‌کند.

    محافظت از «پایگاه‌دانشِ در حال استفاده» دو لایه دارد:

    ۱) **لایه‌ی اصلی و اتمیک:** پرس‌وجوی ``list_stale_beyond_retention`` خودش
       پایگاه‌دانشی را که ``projects.latest_ready_kb_id`` به آن اشاره می‌کند،
       در همان دستور SQL کنار می‌گذارد. این مهم است چون ``SessionLocal`` با
       ``expire_on_commit=False`` ساخته شده و ``session.get(Project, ...)`` می‌تواند
       یک مقدار قدیمیِ کش‌شده در identity map را برگرداند؛ اگر تصمیم «کدام‌ها
       زائدند» و «کدام یکی فعلی است» دو خواندن جدا بودند، یک اجرای هم‌زمان
       می‌توانست اشاره‌گر را بین آن دو جابه‌جا کند و پایگاه‌دانشِ تازه‌فعال‌شده
       حذف شود.
    ۲) **لایه‌ی دفاعی:** بررسی ``kb.id == current_latest`` در همین حلقه، که
       اشاره‌گر را دوباره (در لحظه‌ی حذف) می‌خواند. هزینه‌اش صفر است و اگر روزی
       رفتار پرس‌وجو تغییر کند، همچنان جلوی حذفِ اشاره‌گر فعلی را می‌گیرد.
    """
    settings = get_settings()
    stale = kb_repo.list_stale_beyond_retention(
        session, project_id, keep_count=settings.kb_retention_count
    )
    if not stale:
        return

    project = session.get(Project, project_id)
    current_latest = project.latest_ready_kb_id if project is not None else None

    for kb in stale:
        if kb.id == current_latest:
            logger.warning(
                "retention: refusing to delete the current latest_ready_kb_id %s (project=%s)",
                kb.id,
                project_id,
            )
            continue
        if kb.project_id != project_id:
            # هیچ‌گاه نباید اتفاق بیفتد (پرس‌وجو خودش پروژه را فیلتر می‌کند)،
            # اما یک بررسی دفاعی ارزان است.
            logger.warning(
                "retention: skipping KB %s that does not belong to project %s", kb.id, project_id
            )
            continue
        kb_storage.delete_kb_directory(kb.user_id, kb.project_id, kb.id)
        kb_repo.delete_by_id(session, kb.id)
        logger.info("retention: removed stale checklist KB %s (project=%s)", kb.id, project_id)
