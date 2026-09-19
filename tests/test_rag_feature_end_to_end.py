"""آزمون دودِ سرتاسری قابلیت RAG -- از آپلود تا حذف پروژه، در یک تست پیوسته.

هدف این فایل چیز دیگری از بقیه‌ی تست‌ها است: نه یک واحد، بلکه **کل زنجیره** را
یک‌بار از ابتدا تا انتها می‌گذراند و می‌سنجد که حلقه‌ها به هم وصل‌اند:
آپلود → اجرای چک‌لیست → ایندکس‌سازی پایگاه‌دانش → نقطه‌ی join (تطبیق هوشمند) →
بازخورد نتیجه به همان پایگاه‌دانش → ``ready`` شدن + به‌روزرسانی
``projects.latest_ready_kb_id`` → باز شدن کارگاه چت‌بات → ساخت گفتگو → پرسش
(۲۰۲ + polling پاسخ) → تاریخچه‌ی ماندگار پیام‌ها → سیاست نگهداری در اجرای بعدی →
حذف کامل پروژه.

تنها مرزهایی که جعلی می‌شوند، مرزهای خارجی‌اند (مدل زبانی، embedder، استور
برداری، خواننده‌ی فایل‌ها و خودِ ``pipeline.start_checklist_job`` که به
Tesseract/LibreOffice وابسته است). تمام منطق ارکستراسیون داشبورد -- یعنی
``checklist_kb_service`` (شروع ایندکس‌سازی، join، بازخورد نتایج، ready،
نگهداری)، ``kb_storage`` (نوشتن/خواندن واقعی ``chunks.jsonl``)،
``rag_ai_adapter.build_readonly_knowledge_base``، دروازه‌بندی چت‌بات، مسیرهای
HTTP گفتگو/پرسش و آبشار حذف پروژه -- واقعاً اجرا می‌شود.

نکته‌ی مهم درباره‌ی ``_FakeKnowledgeBase.index_files``: آن تابع دقیقاً همان
مرزی است که پکیج ``rag_chat_module`` فایل‌های ورودی را می‌خواند و به
embedder/استور می‌دهد. جعلی‌کردنش یعنی «خواندن فایل و بردارسازی» انجام نمی‌شود،
اما هر چیز دیگری واقعی می‌ماند: استور «ضبط‌کننده»ی واقعی، ``chunks.jsonl`` واقعی،
و مسیر فقط-خواندنی واقعی که متن را از همان فایل بازمی‌سازد. به همین دلیل
ادعاهای این تست («چت‌بات منبع ``checklist_results`` را می‌بیند») از دلِ همان
رفت‌وبرگشت دیسکی می‌آید، نه از یک شبیه‌سازی در حافظه.
"""
from __future__ import annotations

import io
import time
import types
from pathlib import Path

from sqlalchemy import func, select

from api import storage
from api.config import get_settings
from api.db.base import SessionLocal
from api.db.models import (
    ChatMessage,
    ChatSession,
    KBFile,
    KnowledgeBase,
    Project,
    WorkshopRun,
    WorkshopSetting,
)
from api.repositories import knowledge_bases as kb_repo
from api.services import checklist_kb_service, kb_storage, rag_ai_adapter

CHATBOT_SLUG = "financial_chatbot"
CHECKLIST_RESULTS_KEY = checklist_kb_service.CHECKLIST_RESULTS_KEY


# ---------------------------------------------------------------------------
# داده‌ی چک‌لیست: نتیجه‌ی خام (۲ مورد FALSE) در برابر نتیجه‌ی تطبیق‌شده (۱ مورد)
# ---------------------------------------------------------------------------
def _item(question_id: str, *, status: str, message: str) -> dict:
    """یک مورد چک‌لیست کامل (همان کلیدهایی که ``ChecklistItem`` لازم دارد)."""
    return {
        "question_id": question_id,
        "question_text": f"شرح سوال {question_id}",
        "question_purpose": f"هدف سوال {question_id}",
        "is_evaluable": True,
        "status": status,
        "message": message,
    }


# نتیجه‌ی خام پایپلاین: q1 و q3 نامنطبق‌اند.
ALL_RESULTS = [
    _item("q1", status="FALSE", message="مغایرت در هزینه‌های جاری"),
    _item("q2", status="TRUE", message="منطبق"),
    _item("q3", status="FALSE", message="مغایرت در موجودی انبار"),
    _item("q4", status="TRUE", message="منطبق"),
    _item("q5", status="TRUE", message="منطبق"),
]
RAW_FALSE_QUESTIONS = [item for item in ALL_RESULTS if item["status"] == "FALSE"]

# نتیجه‌ی پس از تطبیق هوشمند: فقط q3 نامنطبق می‌ماند (q1 با استناد به اسناد حل شد).
UPDATED_OPERATIONAL = [
    _item("q1", status="TRUE", message="مغایرت با استناد به ترازنامه رفع شد"),
    _item("q2", status="TRUE", message="منطبق"),
    _item("q3", status="FALSE", message="مغایرت در موجودی انبار (باقی‌مانده)"),
    _item("q4", status="TRUE", message="منطبق"),
    _item("q5", status="TRUE", message="منطبق"),
]
# تنها موردی که باید به گزارش نهایی برسد -- اگر نقطه‌ی join کار نکند، این عدد ۲
# می‌شود و تست شکست می‌خورد (همان چیزی که این تست باید ثابت کند).
EXPECTED_RESOLVED_FALSE_COUNT = 1


def _summary_from(resolved: list[dict]) -> dict:
    false_count = sum(1 for item in resolved if item.get("status") == "FALSE")
    total = len(ALL_RESULTS)
    return {
        "total": total,
        "true_count": total - false_count,
        "false_count": false_count,
        "error_count": 0,
        "manual_count": 0,
        "compliance_rate": round(100.0 * (total - false_count) / total, 1),
    }


# ---------------------------------------------------------------------------
# جایگزین‌های مرز بیرونی
# ---------------------------------------------------------------------------
class _FakeIndexReport:
    def __init__(self, *, files_indexed, total_chunks):
        self.files_indexed = files_indexed
        self.total_chunks = total_chunks
        self.total_files = len(files_indexed)
        self.errors: list[str] = []


class _FakeFrame:
    """جایگزین حداقلی ``pandas.DataFrame`` برای ``kb_storage.serialize_chunk``."""

    def to_dict(self, orient="split"):  # noqa: ARG002 -- همان امضای pandas
        return {"columns": ["شرح", "مقدار"], "index": [0], "data": [["موجودی نقد", 100]]}


def _chunk_metadata_text(file_key: str, path_str: str) -> str:
    """متن «قابل‌جست‌وجو»ی یک ورودی.

    برای فایل‌های Markdown، عیناً محتوای فایل خوانده می‌شود -- چون نتیجه‌ی
    تطبیق‌شده‌ی چک‌لیست به‌صورت یک سند Markdown به پایگاه‌دانش اضافه می‌شود و تست
    باید بتواند واقعاً محتوایش را در ``chunks.jsonl`` ببیند.
    """
    path = Path(path_str)
    if path.suffix.lower() == ".md":
        return path.read_text(encoding="utf-8")
    return f"metadata:{file_key}"


def _make_chunk(file_key: str, path_str: str):
    return types.SimpleNamespace(
        chunk_id=f"{file_key}-0",
        file_key=file_key,
        source_sheet_names=[Path(path_str).stem],
        metadata_text=_chunk_metadata_text(file_key, path_str),
        dataframe=_FakeFrame(),
    )


class _FakeVectorStore:
    """جایگزین ``ChromaVectorStore`` -- فقط حافظه، بدون chromadb.

    همان شکل واقعی را دارد (``_chunk_cache`` + ``add``) تا پوشش «ضبط‌کننده»ی
    خودِ داشبورد بتواند دورش بنشیند و ``chunks.jsonl`` را واقعاً بنویسد.
    """

    def __init__(self, collection_name, persist_directory):
        self.collection_name = collection_name
        self.persist_directory = persist_directory
        self._chunk_cache: dict[str, object] = {}

    def add(self, chunk, vector):  # noqa: ARG002 -- امضای VectorStore
        self._chunk_cache[chunk.chunk_id] = chunk

    def search(self, query_vector, top_k):  # noqa: ARG002
        return []


class _FakeKnowledgeBase:
    """جایگزین ``KnowledgeBase`` پکیج RAG.

    ``index_files`` = مرزِ «خواندن فایل‌های ورودی + بردارسازی» (جعلی).
    ``list_sheets``/``is_ready`` عیناً همان منطق چندخطی نسخه‌ی واقعی‌اند، چون
    ``build_readonly_knowledge_base`` (که واقعی و بدون تغییر اجرا می‌شود) این
    صفت‌ها را روی همین شیء می‌نشاند.
    """

    def __init__(self, embedder=None, vector_store_factory=None, segmenter=None):
        self.embedder = embedder
        self.vector_store_factory = vector_store_factory
        self._chunks_by_id: dict[str, object] = {}
        self._store = None
        self._fitted = False

    def index_files(self, file_registry, sheet_descriptions=None):  # noqa: ARG002
        store = self.vector_store_factory()
        files_indexed = {}
        for file_key, path_str in file_registry.items():
            chunk = _make_chunk(file_key, path_str)
            store.add(chunk, [0.0])
            self._chunks_by_id[chunk.chunk_id] = chunk
            files_indexed[file_key] = {"status": "ok", "sheets": 1, "chunks": 1}
        self._store = store
        self._fitted = True
        return _FakeIndexReport(files_indexed=files_indexed, total_chunks=len(files_indexed))

    def list_sheets(self):
        out: dict[str, set] = {}
        for chunk in self._chunks_by_id.values():
            out.setdefault(chunk.file_key, set()).update(chunk.source_sheet_names)
        return {key: sorted(names) for key, names in out.items()}

    def is_ready(self) -> bool:
        return self._fitted and self._store is not None


class _FakeFullRunReport:
    """خروجی جعلی ``run_full_audit`` (خودِ تطبیق هوشمند = فراخوانی مدل)."""

    def __init__(self, updated_operational):
        self.updated_operational = updated_operational

    def to_dict(self):
        return {
            "summary": {"total": len(self.updated_operational), "false_count": 1},
            "updated_operational": self.updated_operational,
        }


class _FakeAnswer:
    """خروجی جعلی ``chat_ask`` -- منابعش از دلِ پایگاه‌دانش واقعی ساخته می‌شود."""

    def __init__(self, sources):
        self._sources = sources

    def to_dict(self):
        return {
            "answer": "بر پایه‌ی اسناد این پروژه، پاسخ آزمایشی است.",
            "confidence": "high",
            "sources": self._sources,
            "route_reasoning": "مسیر آزمایشی",
        }


class _ChecklistGate:
    """جایگزین ``pipeline.start_checklist_job``: job را تا دستور تست باز نگه می‌دارد.

    این جعلی‌سازی صرفاً به‌خاطر وابستگی‌های سنگین پایپلاین (استخراج اکسل،
    Tesseract، LibreOffice) است -- و بیرونی‌ترین حلقه‌ی اتصال RAG را حفظ می‌کند:
    همان ``resolve_false_questions`` که ``run_full_pipeline`` واقعی هم صدا می‌زند.
    """

    def __init__(self):
        self.job: dict | None = None
        self.resolve = None
        self.resolved: list[dict] | None = None

    def starter(self, job, file_paths, workdir, **kwargs):  # noqa: ARG002
        job["status"] = "running"
        job["stage"] = "در حال پردازش (آزمون سرتاسری)"
        job["logs"] = ["شروع پردازش آزمایشی"]
        self.job = job
        self.resolve = kwargs.get("resolve_false_questions")

    def release(self) -> None:
        assert self.resolve is not None, "نقطه‌ی join به job پاس داده نشده است"
        assert self.job is not None, "هیچ اجرایی شروع نشده است"
        self.resolved = self.resolve(RAW_FALSE_QUESTIONS, ALL_RESULTS)
        self.job["result"] = {
            "checklist_results": UPDATED_OPERATIONAL,
            "false_questions": RAW_FALSE_QUESTIONS,
            "resolved_false_questions": self.resolved,
            "summary": _summary_from(self.resolved),
            "warnings": [],
            "committee_report": {
                "docx_bytes": b"committee-report-bytes",
                "docx_filename": "گزارش_کمیسیون.docx",
                "used_audit_report_text": False,
            },
            "elapsed_seconds": 1.5,
        }
        self.job["status"] = "done"


def _kb_directory(project_id: int, user_id: int, kb_id: str) -> Path:
    """مسیر خام پوشه‌ی یک پایگاه‌دانش -- بدون ساختن آن (برخلاف ``kb_storage.path_for``)."""
    return Path(get_settings().vector_store_root) / str(user_id) / str(project_id) / kb_id


def _chunk_records_by_file_key(user_id: int, project_id: int, kb_id: str) -> dict[str, str]:
    return {
        str(record.get("file_key")): str(record.get("metadata_text", ""))
        for record in kb_storage.read_chunk_records(user_id, project_id, kb_id)
    }


def _count(model, **filters) -> int:
    with SessionLocal() as session:
        stmt = select(func.count()).select_from(model)
        for column, value in filters.items():
            stmt = stmt.where(getattr(model, column) == value)
        return int(session.scalar(stmt) or 0)


def _ask_and_wait(auth_client, project_id: int, chat_id: str, question: str, timeout: float = 15.0) -> dict:
    """پرسش می‌فرستد (۲۰۲) و تا نهایی‌شدن پاسخ همان پیام polling می‌کند.

    دقیقاً همان کاری که ``web/static/js/financial_chatbot.js`` می‌کند: پاسخ‌دهی
    یک کار پس‌زمینه است، پس مسیر HTTP فوراً برمی‌گردد و نتیجه از ردیف پیام خوانده
    می‌شود.
    """
    response = auth_client.post(
        f"/api/projects/{project_id}/{CHATBOT_SLUG}/sessions/{chat_id}/ask",
        json={"question": question},
    )
    assert response.status_code == 202, response.text
    started = response.json()
    deadline = time.time() + timeout
    while time.time() < deadline:
        polled = auth_client.get(
            f"/api/projects/{project_id}/{CHATBOT_SLUG}/sessions/{chat_id}"
            f"/messages/{started['assistant_message_id']}"
        )
        assert polled.status_code == 200, polled.text
        row = polled.json()
        if row["status"] in ("complete", "failed"):
            return row
        time.sleep(0.02)
    raise AssertionError("پاسخ چت‌بات در مهلت مقرر نهایی نشد")


def _xlsx(slot_key: str):
    return (
        slot_key,
        (
            f"{slot_key}.xlsx",
            io.BytesIO(b"fake-xlsx-bytes"),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
    )


def _start_checklist_run(auth_client, project_id: int) -> dict:
    response = auth_client.post(
        f"/api/projects/{project_id}/checklist/jobs",
        data={"entity_name": "سازمان آزمون"},
        files=dict(
            [_xlsx("revised_budget"), _xlsx("financial_statements"), _xlsx("balance_sheet")]
        ),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _stub_external_boundaries(monkeypatch) -> dict:
    """تنها مرزهای بیرونی را جعلی می‌کند و اشیای ضبط‌شده را برمی‌گرداند."""
    captured: dict = {"ask_calls": []}

    monkeypatch.setattr(
        rag_ai_adapter, "build_embedder", lambda **kw: (_FakeEmbedder(), "cpu")
    )
    # مرز مدل زبانی: هیچ کلاینت OpenAI واقعی (و هیچ درخواست شبکه‌ای) ساخته نمی‌شود.
    monkeypatch.setattr(
        rag_ai_adapter,
        "build_llm_client",
        lambda session, project_id, workshop_slug=None, **kw: object(),
    )

    # استور برداری: ``build_recording_vector_store_factory`` واقعی می‌ماند (تا
    # ``chunks.jsonl`` واقعاً نوشته شود) و فقط کلاس درونی‌اش جعلی می‌شود.
    real_store_factory = rag_ai_adapter.build_vector_store_factory

    def _store_factory(user_id, project_id, kb_id, **kwargs):
        if kwargs.get("store_cls") is None:
            kwargs["store_cls"] = _FakeVectorStore
        return real_store_factory(user_id, project_id, kb_id, **kwargs)

    monkeypatch.setattr(rag_ai_adapter, "build_vector_store_factory", _store_factory)

    # مسیر فقط-خواندنی: تابع واقعی اجرا می‌شود (متن را از ``chunks.jsonl``
    # بازمی‌سازد) و فقط embedder/استور تزریق می‌شوند.
    real_readonly = rag_ai_adapter.build_readonly_knowledge_base

    def _readonly(user_id, project_id, kb_id, **kwargs):
        kwargs.setdefault("embedder", _FakeEmbedder())
        if kwargs.get("store_cls") is None:
            kwargs["store_cls"] = _FakeVectorStore
        knowledge_base = real_readonly(user_id, project_id, kb_id, **kwargs)
        captured["readonly_calls"] = captured.get("readonly_calls", []) + [
            (user_id, project_id, kb_id)
        ]
        return knowledge_base

    monkeypatch.setattr(rag_ai_adapter, "build_readonly_knowledge_base", _readonly)

    # ``KnowledgeBase`` (خواندن فایل + بردارسازی) و ``run_full_audit``/``chat_ask``
    # (فراخوانی مدل) جعلی می‌شوند؛ بقیه‌ی زنجیره واقعی است.
    import llm_variable_resolver
    import llm_variable_resolver.full_run as full_run_module
    import llm_variable_resolver.knowledge_base as kb_module

    monkeypatch.setattr(kb_module, "KnowledgeBase", _FakeKnowledgeBase)
    monkeypatch.setattr(
        full_run_module,
        "run_full_audit",
        lambda **kwargs: _FakeFullRunReport(UPDATED_OPERATIONAL),
    )

    def _chat_ask(question, knowledge_base, llm_client, **kwargs):  # noqa: ARG001
        # منابع از دلِ chunk هایی ساخته می‌شوند که همین حالا از ``chunks.jsonl``
        # بازسازی شده‌اند -- یعنی محتوای snippet واقعاً از دیسک آمده است.
        chunks = list(getattr(knowledge_base, "_chunks_by_id", {}).values())
        sources = [
            {
                "file_key": chunk.file_key,
                "sheet_names": list(chunk.source_sheet_names),
                "chunk_id": chunk.chunk_id,
                "similarity": 0.91,
                "snippet": str(chunk.metadata_text)[:800],
            }
            for chunk in chunks
        ]
        captured["ask_calls"].append(
            {
                "question": question,
                "sheet_catalog": kwargs.get("sheet_catalog"),
                "sheets": knowledge_base.list_sheets(),
                "use_router": kwargs.get("use_router"),
            }
        )
        return _FakeAnswer(sources)

    monkeypatch.setattr(llm_variable_resolver, "chat_ask", _chat_ask)
    return captured


class _FakeEmbedder:
    """جایگزین ``SentenceTransformerEmbedder`` -- هیچ مدلی بار نمی‌شود."""

    resolved_device = "cpu"

    def fit(self, texts):  # noqa: ARG002
        return self

    def embed(self, text):  # noqa: ARG002
        return [0.0]


def test_the_whole_rag_feature_works_end_to_end_and_leaves_nothing_behind(
    auth_client, db_session, project, user, monkeypatch, wait_for_run
):
    # --- ترتیب صفر: نگهداری روی «فقط جدیدترین» تا اجرای دوم، اجرای اول را هرس کند.
    fake_settings = types.SimpleNamespace(
        vector_store_root=get_settings().vector_store_root,
        embedding_model="fake-embedding-model",
        kb_retention_count=1,
    )
    monkeypatch.setattr(checklist_kb_service, "get_settings", lambda: fake_settings)

    captured = _stub_external_boundaries(monkeypatch)
    gate = _ChecklistGate()
    import pipeline

    monkeypatch.setattr(pipeline, "start_checklist_job", gate.starter)

    assert len(RAW_FALSE_QUESTIONS) == 2, "پیش‌شرط تست: نتیجه‌ی خام باید دو مورد نامنطبق داشته باشد"

    # =====================================================================
    # ۱) اجرای اول چک‌لیست: آپلود -> job -> نقطه‌ی join -> گزارش
    # =====================================================================
    first = _start_checklist_run(auth_client, project.id)
    assert gate.resolve is not None, "نقطه‌ی join باید به job پاس داده شود"

    gate.release()

    resolved = gate.resolved
    assert resolved is not None
    # گزارش با نتیجه‌ی *تطبیق‌شده* ساخته می‌شود، نه با موارد خام.
    assert [item["question_id"] for item in resolved] == ["q3"]
    assert all(item["status"] == "FALSE" for item in resolved)

    first_run = wait_for_run(project.id, first["run_id"])
    assert first_run["status"] == "done"
    assert first_run["result_summary"]["false_count"] == EXPECTED_RESOLVED_FALSE_COUNT
    assert first_run["result_summary"]["false_count"] != len(RAW_FALSE_QUESTIONS)
    assert first_run["result_summary"]["entity_name"] == "سازمان آزمون"
    assert first_run["result_file_path"], "گزارش کمیسیون باید روی دیسک ذخیره شده باشد"

    # =====================================================================
    # ۲) پایگاه‌دانش: ایندکس‌شده، شامل نتیجه‌ی چک‌لیست، ready و اشاره‌گرشده
    # =====================================================================
    with SessionLocal() as session:
        kbs = kb_repo.list_by_project(session, project.id)
        assert len(kbs) == 1, "اجرای اول باید دقیقاً یک پایگاه‌دانش بسازد"
        kb_one = kbs[0]
        kb_one_id = kb_one.id
        assert kb_one.status == kb_repo.READY
        file_keys_one = {row.file_key for row in kb_repo.list_files(session, kb_one_id)}
        project_row = session.get(Project, project.id)
        assert project_row.latest_ready_kb_id == kb_one_id

    assert {
        "revised_budget",
        "financial_statements",
        "balance_sheet",
        checklist_kb_service.CHECKLIST_DEFINITION_KEY,
        CHECKLIST_RESULTS_KEY,
    } <= file_keys_one

    chunks_one = _chunk_records_by_file_key(user.id, project.id, kb_one_id)
    assert chunks_one["revised_budget"] == "metadata:revised_budget"
    assert chunks_one["financial_statements"] == "metadata:financial_statements"
    # نتیجه‌ی تطبیق‌شده واقعاً به‌صورت متن در همان پایگاه‌دانش ذخیره شده است:
    # فقط مورد باقی‌مانده (q3) در جدول «موارد عدم تطابق» دیده می‌شود، نه هر دو مورد خام.
    checklist_results_text = chunks_one[CHECKLIST_RESULTS_KEY]
    assert "موارد عدم تطابق (FALSE)" in checklist_results_text
    assert "q3" in checklist_results_text
    assert "q1" not in checklist_results_text

    # پوشه‌ی روی دیسک همان پایگاه‌دانش ساخته شده و غیرخالی است.
    kb_one_dir = _kb_directory(project.id, user.id, kb_one_id)
    assert kb_one_dir.is_dir()
    assert (kb_one_dir / kb_storage.MANIFEST_FILENAME).is_file()
    assert (kb_one_dir / kb_storage.CHUNKS_FILENAME).is_file()

    # =====================================================================
    # ۳) کارگاه چت‌بات: از حالت دروازه‌بندی‌شده به رابط گفتگو
    # =====================================================================
    page = auth_client.get(f"/projects/{project.id}/{CHATBOT_SLUG}")
    assert page.status_code == 200
    body = page.text
    assert "psa-chat-form" in body
    assert "هنوز پایگاه‌دانشی برای این پروژه ساخته نشده است" not in body

    # =====================================================================
    # ۴) گفتگو + پرسش: پاسخ پس‌زمینه، تاریخچه‌ی ماندگار
    # =====================================================================
    session_response = auth_client.post(
        f"/api/projects/{project.id}/{CHATBOT_SLUG}/sessions",
        json={"title": "بررسی موجودی نقد"},
    )
    assert session_response.status_code == 201, session_response.text
    chat_id = session_response.json()["id"]
    assert chat_id

    first_answer = _ask_and_wait(auth_client, project.id, chat_id, "کدام موارد چک‌لیست رد شدند؟")
    assert first_answer["status"] == "complete", first_answer
    assert first_answer["content"].strip()
    assert first_answer["confidence"] == "high"
    assert captured["ask_calls"][-1]["use_router"] is True
    # اولین پرسش این گفتگو زمینه‌ی قبلی ندارد (سرور تاریخچه را از ردیف‌های ذخیره‌شده
    # می‌سازد، نه از payload کلاینت).
    assert "گفتگوی قبلی" not in captured["ask_calls"][-1]["question"]

    # پاسخ از پایگاه‌دانشِ همان پروژه آمده و همان دو سند ایندکس‌شده را می‌بیند.
    assert captured["readonly_calls"][-1] == (user.id, project.id, kb_one_id)
    assert {source["file_key"] for source in first_answer["sources"]} >= {
        "revised_budget",
        CHECKLIST_RESULTS_KEY,
    }
    checklist_source = next(
        source
        for source in first_answer["sources"]
        if source["file_key"] == CHECKLIST_RESULTS_KEY
    )
    assert "موارد عدم تطابق (FALSE)" in checklist_source["snippet"]
    # کاتالوگ روتر از شیت‌های واقعاً ایندکس‌شده ساخته شده (نه از یک فهرست ثابت).
    assert set(captured["ask_calls"][-1]["sheet_catalog"]) >= {"revised_budget", CHECKLIST_RESULTS_KEY}

    # پرسش دوم در همان گفتگو: زمینه‌ی چندنوبتی از پیام‌های *ذخیره‌شده* می‌آید.
    second_answer = _ask_and_wait(auth_client, project.id, chat_id, "و موجودی نقد چقدر بود؟")
    assert second_answer["status"] == "complete", second_answer
    assert "گفتگوی قبلی" in captured["ask_calls"][-1]["question"]
    assert "کدام موارد چک‌لیست رد شدند؟" in captured["ask_calls"][-1]["question"]

    # ردیف‌های گفتگو واقعاً ماندگارند: باز کردن دوباره‌ی گفتگو همه‌ی پیام‌ها را
    # به ترتیب برمی‌گرداند -- همان چیزی که پس از refresh/بازگشت کاربر دیده می‌شود.
    with SessionLocal() as session:
        chat_row = session.get(ChatSession, chat_id)
        assert chat_row is not None and chat_row.title == "بررسی موجودی نقد"
        persisted = session.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == chat_id)
            .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
        ).all()
    assert [(row.role, row.status) for row in persisted] == [
        ("user", "complete"),
        ("assistant", "complete"),
        ("user", "complete"),
        ("assistant", "complete"),
    ]
    assert persisted[0].content == "کدام موارد چک‌لیست رد شدند؟"
    assert persisted[1].content == first_answer["content"]

    # =====================================================================
    # ۵) اجرای دوم چک‌لیست: سیاست نگهداری، پایگاه‌دانش اجرای اول را هرس می‌کند
    # =====================================================================
    second = _start_checklist_run(auth_client, project.id)
    gate.release()

    second_run = wait_for_run(project.id, second["run_id"])
    assert second_run["status"] == "done"
    assert second_run["result_summary"]["false_count"] == EXPECTED_RESOLVED_FALSE_COUNT

    with SessionLocal() as session:
        kbs_after = kb_repo.list_by_project(session, project.id)
        assert len(kbs_after) == 1, "با PSa_KB_RETENTION_COUNT=1 فقط جدیدترین می‌ماند"
        kb_two_id = kbs_after[0].id
        assert kb_two_id != kb_one_id
        assert session.get(Project, project.id).latest_ready_kb_id == kb_two_id
        assert kb_repo.get_by_id(session, kb_one_id, user_id=user.id, project_id=project.id) is None

    assert not kb_one_dir.exists(), "پوشه‌ی پایگاه‌دانش هرس‌شده باید از دیسک هم پاک شود"
    kb_two_dir = _kb_directory(project.id, user.id, kb_two_id)
    assert kb_two_dir.is_dir()
    assert _chunk_records_by_file_key(user.id, project.id, kb_two_id)

    # چت‌بات حالا به نسخه‌ی جدید اشاره می‌کند (گفتگوی قبلی دست‌نخورده می‌ماند).
    third_answer = _ask_and_wait(auth_client, project.id, chat_id, "پایگاه‌دانش فعلی کدام است؟")
    assert third_answer["status"] == "complete", third_answer
    assert captured["readonly_calls"][-1] == (user.id, project.id, kb_two_id)

    # =====================================================================
    # ۶) حذف پروژه: همه‌چیز می‌رود -- ردیف‌ها، گفتگوها، فایل‌ها و پوشه‌ها
    # =====================================================================
    with SessionLocal() as session:
        assert session.get(Project, project.id) is not None
        assert kb_repo.list_by_project(session, project.id)

    delete_response = auth_client.post(
        f"/projects/{project.id}/delete",
        data={"confirm": "delete"},
        follow_redirects=False,
    )
    assert delete_response.status_code == 303

    with SessionLocal() as session:
        assert session.get(Project, project.id) is None
        assert session.scalars(
            select(KnowledgeBase).where(KnowledgeBase.project_id == project.id)
        ).all() == []

    assert _count(KnowledgeBase, project_id=project.id) == 0
    assert _count(KBFile, knowledge_base_id=kb_two_id) == 0
    assert _count(ChatSession, project_id=project.id) == 0
    assert _count(ChatMessage, project_id=project.id) == 0
    assert _count(WorkshopRun, project_id=project.id) == 0
    assert _count(WorkshopSetting, project_id=project.id) == 0

    assert not kb_two_dir.exists()
    assert not (Path(get_settings().vector_store_root) / str(user.id) / str(project.id)).exists()
    assert not storage.project_dir(project.id).exists()
