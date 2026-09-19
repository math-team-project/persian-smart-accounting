"""تست‌های ``api/services/checklist_kb_service.py``.

هیچ‌کدام از این تست‌ها به یک LLM/Embedder/Chroma واقعی نیاز ندارند:
``KnowledgeBase.index_files``، ``run_full_audit`` و
``rag_ai_adapter.build_llm_client``/``build_embedder``/``build_vector_store_factory``
همه با جایگزین‌های جعلی (stub) پوشانده می‌شوند -- دقیقاً همان الگوی
``tests/test_rag_ai_adapter.py`` و ``tests/test_concurrent_jobs.py``.
"""
from __future__ import annotations

import threading
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from api.config import get_settings
from api.db.base import SessionLocal
from api.db.models import Project
from api.repositories import knowledge_bases as kb_repo
from api.repositories import projects as projects_repo
from api.services import checklist_kb_service, financial_chatbot_service, kb_storage, rag_ai_adapter

pytestmark = pytest.mark.usefixtures("_fresh_state")


class _FakeIndexReport:
    def __init__(self, *, files_indexed, total_chunks, errors=None):
        self.files_indexed = files_indexed
        self.total_chunks = total_chunks
        self.total_files = len(files_indexed)
        self.errors = errors or []


class _FakeFrame:
    """جایگزین حداقلی ``pandas.DataFrame`` برای ``kb_storage.serialize_chunk``."""

    def to_dict(self, orient="split"):  # noqa: ARG002 -- همان امضای pandas
        return {"columns": ["شرح", "مقدار"], "index": [0], "data": [["موجودی نقد", 100]]}


def _fake_chunk(file_key: str, index: int = 0):
    """یک ``Chunk`` حداقلی که ``kb_storage`` می‌تواند سریال‌اش کند."""
    return types.SimpleNamespace(
        chunk_id=f"{file_key}-{index}",
        file_key=file_key,
        source_sheet_names=["ترازنامه"],
        metadata_text=f"metadata:{file_key}",
        dataframe=_FakeFrame(),
    )


class _FakeVectorStore:
    """جایگزین ``ChromaVectorStore`` -- فقط حافظه، بدون chromadb و بدون دیسک.

    عمداً همین شکل را دارد (``_chunk_cache`` + ``add``/``search``) تا مسیر واقعی
    ``build_recording_vector_store_factory`` بتواند دورش بنشیند و ``chunks.jsonl``
    را واقعاً بنویسد؛ وگرنه هیچ تستی این sidecar را تمرین نمی‌کرد.
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
    """جایگزین ``llm_variable_resolver.knowledge_base.KnowledgeBase``."""

    fail_files = frozenset()
    empty = False

    def __init__(self, embedder, vector_store_factory, segmenter=None):
        self.embedder = embedder
        self.vector_store_factory = vector_store_factory

    def index_files(self, file_registry, sheet_descriptions=None):  # noqa: ARG002
        if type(self).empty:
            return _FakeIndexReport(files_indexed={}, total_chunks=0, errors=["nothing to index"])
        # استور واقعیِ «ضبط‌کننده» ساخته می‌شود تا متن هر chunk در ``chunks.jsonl``
        # همین پایگاه‌دانش نوشته شود -- یعنی تست هم sidecar و هم جداسازی متن
        # بین دو پایگاه‌دانش را واقعاً می‌سنجد (نه فقط وجود پوشه را).
        store = self.vector_store_factory()
        files_indexed = {}
        total_chunks = 0
        for key in file_registry:
            if key in type(self).fail_files:
                files_indexed[key] = {"status": "error", "error": "boom"}
                continue
            store.add(_fake_chunk(key), [0.0])
            files_indexed[key] = {"status": "ok", "sheets": 1, "chunks": 2}
            total_chunks += 2
        return _FakeIndexReport(files_indexed=files_indexed, total_chunks=total_chunks)


class _FakeFullRunReport:
    def __init__(self, updated_operational, summary=None):
        self.updated_operational = updated_operational
        self.summary = summary or {"total": len(updated_operational)}

    def to_dict(self):
        return {"summary": self.summary, "updated_operational": self.updated_operational}


@pytest.fixture()
def isolated_vector_root(tmp_path, monkeypatch):
    fake_settings = types.SimpleNamespace(
        vector_store_root=tmp_path / "vector_stores",
        embedding_model="fake-embedding-model",
        kb_retention_count=get_settings().kb_retention_count,
    )
    monkeypatch.setattr(kb_storage, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(rag_ai_adapter, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(checklist_kb_service, "get_settings", lambda: fake_settings)
    return tmp_path / "vector_stores"


@pytest.fixture(autouse=True)
def _stub_rag_adapter(monkeypatch):
    """جلوگیری از بارگیری واقعی مدل embedding/LLM در تمام تست‌های این فایل."""
    monkeypatch.setattr(
        rag_ai_adapter, "build_embedder", lambda **kw: (object(), "cpu")
    )
    monkeypatch.setattr(
        rag_ai_adapter, "build_llm_client", lambda session, project_id, workshop_slug=None, **kw: object()
    )
    # لایه‌ی بردار هم جعلی می‌شود: ``build_recording_vector_store_factory`` (کد
    # واقعی داشبورد) هنوز ``chunks.jsonl`` را می‌نویسد، اما هیچ Chroma/دیسک
    # واقعی‌ای درگیر نمی‌شود.
    real_factory = rag_ai_adapter.build_vector_store_factory

    def _factory(user_id, project_id, kb_id, **kwargs):
        # ``build_recording_vector_store_factory`` مقدار ``store_cls=None`` را
        # صریحاً پاس می‌دهد، پس ``setdefault`` کافی نیست.
        if kwargs.get("store_cls") is None:
            kwargs["store_cls"] = _FakeVectorStore
        return real_factory(user_id, project_id, kb_id, **kwargs)

    monkeypatch.setattr(rag_ai_adapter, "build_vector_store_factory", _factory)


@pytest.fixture(autouse=True)
def _stub_knowledge_base(monkeypatch):
    _FakeKnowledgeBase.fail_files = frozenset()
    _FakeKnowledgeBase.empty = False
    import llm_variable_resolver.knowledge_base as kb_module

    monkeypatch.setattr(kb_module, "KnowledgeBase", _FakeKnowledgeBase)
    monkeypatch.setattr(checklist_kb_service, "get_settings", get_settings)
    return _FakeKnowledgeBase


def _wait_until(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _uploaded_file_paths(tmp_path) -> dict:
    budget = tmp_path / "revised_budget.xlsx"
    budget.write_bytes(b"fake")
    statements = tmp_path / "financial_statements.xlsx"
    statements.write_bytes(b"fake")
    return {
        "revised_budget": budget,
        "financial_statements": statements,
        "balance_sheet": None,
        "credit_approvals": None,
        "budget_law": None,
        "audit_report_doc": None,
    }


def test_start_indexing_creates_a_fresh_kb_row_and_directory_on_every_run(
    isolated_vector_root, db_session, project, user, tmp_path
):
    file_paths = _uploaded_file_paths(tmp_path)

    join1 = checklist_kb_service.start_indexing(
        user_id=user.id, project_id=project.id, file_paths=file_paths
    )
    assert _wait_until(lambda: join1.kb_id is not None)

    join2 = checklist_kb_service.start_indexing(
        user_id=user.id, project_id=project.id, file_paths=file_paths
    )
    assert _wait_until(lambda: join2.kb_id is not None)

    assert join1.kb_id != join2.kb_id
    dir1 = kb_storage.path_for(user.id, project.id, join1.kb_id)
    dir2 = kb_storage.path_for(user.id, project.id, join2.kb_id)
    assert dir1 != dir2
    assert dir1.exists() and dir2.exists()

    with SessionLocal() as session:
        kb1 = kb_repo.get_by_id(session, join1.kb_id, user_id=user.id, project_id=project.id)
        kb2 = kb_repo.get_by_id(session, join2.kb_id, user_id=user.id, project_id=project.id)
    assert kb1 is not None and kb2 is not None
    assert kb1.id != kb2.id


def test_indexing_failure_falls_back_to_the_raw_checklist_result(
    isolated_vector_root, db_session, project, user, tmp_path, _stub_knowledge_base
):
    _stub_knowledge_base.empty = True  # هیچ چانکی ایندکس نمی‌شود -> شکست
    file_paths = _uploaded_file_paths(tmp_path)

    join = checklist_kb_service.start_indexing(
        user_id=user.id, project_id=project.id, file_paths=file_paths
    )

    raw_false = [{"question_id": "q1", "status": "FALSE", "message": "raw"}]
    resolved = join.resolve_false_questions(raw_false, checklist_results=raw_false)

    assert resolved == raw_false

    with SessionLocal() as session:
        kb = kb_repo.get_by_id(session, join.kb_id, user_id=user.id, project_id=project.id)
        assert kb.status == kb_repo.FAILED


def test_resolver_updated_operational_reaches_the_caller_when_indexing_succeeds(
    isolated_vector_root, db_session, project, user, tmp_path, monkeypatch
):
    file_paths = _uploaded_file_paths(tmp_path)
    join = checklist_kb_service.start_indexing(
        user_id=user.id, project_id=project.id, file_paths=file_paths
    )
    assert _wait_until(lambda: join.kb_id is not None)

    raw_false = [{"question_id": "q1", "status": "FALSE", "message": "raw"}]
    updated_operational = [
        {"question_id": "q1", "status": "TRUE", "message": "resolved via RAG"},
        {"question_id": "q2", "status": "FALSE", "message": "still non-conforming"},
    ]

    import llm_variable_resolver.full_run as full_run_module

    monkeypatch.setattr(
        full_run_module,
        "run_full_audit",
        lambda **kwargs: _FakeFullRunReport(updated_operational),
    )

    resolved = join.resolve_false_questions(raw_false, checklist_results=raw_false)

    assert resolved == [updated_operational[1]]
    assert resolved != raw_false


def test_kb_is_only_marked_ready_after_the_checklist_results_feedback_step(
    isolated_vector_root, db_session, project, user, tmp_path, monkeypatch
):
    file_paths = _uploaded_file_paths(tmp_path)
    join = checklist_kb_service.start_indexing(
        user_id=user.id, project_id=project.id, file_paths=file_paths
    )
    assert _wait_until(lambda: join.kb_id is not None)

    # بلافاصله پس از ایندکس‌سازی اولیه (پیش از resolver)، هنوز "indexing" است.
    with SessionLocal() as session:
        kb = kb_repo.get_by_id(session, join.kb_id, user_id=user.id, project_id=project.id)
        assert kb.status == kb_repo.INDEXING
        project_row = session.get(Project, project.id)
        assert project_row.latest_ready_kb_id is None

    import llm_variable_resolver.full_run as full_run_module

    updated_operational = [{"question_id": "q1", "status": "FALSE", "message": "x"}]
    monkeypatch.setattr(
        full_run_module, "run_full_audit", lambda **kwargs: _FakeFullRunReport(updated_operational)
    )

    join.resolve_false_questions(updated_operational, checklist_results=updated_operational)

    with SessionLocal() as session:
        kb = kb_repo.get_by_id(session, join.kb_id, user_id=user.id, project_id=project.id)
        assert kb.status == kb_repo.READY
        project_row = session.get(Project, project.id)
        assert project_row.latest_ready_kb_id == join.kb_id


def test_retention_deletes_directory_and_rows_but_spares_latest_and_other_projects(
    tmp_path, db_session, user, second_user, monkeypatch
):
    from api.repositories import projects as projects_repo

    fake_settings = types.SimpleNamespace(
        vector_store_root=tmp_path / "vector_stores",
        embedding_model="fake-embedding-model",
        kb_retention_count=1,  # فقط جدیدترین نگه داشته شود
    )
    monkeypatch.setattr(kb_storage, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(checklist_kb_service, "get_settings", lambda: fake_settings)

    project_a = projects_repo.create(db_session, user.id, "پروژه الف")
    project_b = projects_repo.create(db_session, second_user.id, "پروژه ب")

    def _make_ready_kb(project_id, owner_id):
        kb = kb_repo.create(
            db_session,
            project_id=project_id,
            user_id=owner_id,
            embedding_model="fake",
            embedding_device="cpu",
            chroma_collection_name="kb-x",
            chroma_persist_dir=kb_storage.relative_path_for(owner_id, project_id, "placeholder"),
        )
        kb_storage.path_for(owner_id, project_id, kb.id)  # واقعاً پوشه را روی دیسک بساز
        kb_repo.mark_ready_and_update_project_pointer(db_session, kb.id)
        return kb

    old_kb = _make_ready_kb(project_a.id, user.id)
    new_kb = _make_ready_kb(project_a.id, user.id)
    other_project_kb = _make_ready_kb(project_b.id, second_user.id)

    old_dir = kb_storage.path_for(user.id, project_a.id, old_kb.id)
    new_dir = kb_storage.path_for(user.id, project_a.id, new_kb.id)
    other_dir = kb_storage.path_for(second_user.id, project_b.id, other_project_kb.id)
    assert old_dir.exists() and new_dir.exists() and other_dir.exists()

    checklist_kb_service._apply_retention(db_session, project_a.id)

    assert not old_dir.exists()
    assert kb_repo.get_by_id(db_session, old_kb.id, user_id=user.id, project_id=project_a.id) is None

    # جدیدترین KB این پروژه (که latest_ready_kb_id است) دست‌نخورده می‌ماند.
    assert new_dir.exists()
    assert kb_repo.get_by_id(db_session, new_kb.id, user_id=user.id, project_id=project_a.id) is not None

    # پایگاه‌دانش پروژه‌ی دیگر اصلاً لمس نمی‌شود.
    assert other_dir.exists()
    assert (
        kb_repo.get_by_id(db_session, other_project_kb.id, user_id=second_user.id, project_id=project_b.id)
        is not None
    )


def test_two_concurrent_checklist_runs_get_independent_knowledge_bases(
    isolated_vector_root, db_session, project, user, tmp_path
):
    (tmp_path / "run1").mkdir()
    (tmp_path / "run2").mkdir()
    file_paths_1 = _uploaded_file_paths(tmp_path / "run1")
    file_paths_2 = _uploaded_file_paths(tmp_path / "run2")

    results: dict[str, object] = {}

    def _start(key, file_paths):
        results[key] = checklist_kb_service.start_indexing(
            user_id=user.id, project_id=project.id, file_paths=file_paths
        )

    t1 = threading.Thread(target=_start, args=("a", file_paths_1))
    t2 = threading.Thread(target=_start, args=("b", file_paths_2))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    join_a, join_b = results["a"], results["b"]
    assert _wait_until(lambda: join_a.kb_id is not None)
    assert _wait_until(lambda: join_b.kb_id is not None)

    assert join_a.kb_id != join_b.kb_id
    dir_a = kb_storage.path_for(user.id, project.id, join_a.kb_id)
    dir_b = kb_storage.path_for(user.id, project.id, join_b.kb_id)
    assert dir_a != dir_b
    assert dir_a.exists() and dir_b.exists()


# ---------------------------------------------------------------------------
# جداسازی محتوا بین دو اجرای هم‌زمان در یک پروژه
# ---------------------------------------------------------------------------
_OPTIONAL_SLOT_KEYS = (
    "revised_budget",
    "financial_statements",
    "balance_sheet",
    "credit_approvals",
    "budget_law",
    "audit_report_doc",
)


def _file_paths_for_slots(directory: Path, slots: tuple[str, ...]) -> dict:
    """فقط اسلات‌های خواسته‌شده را پر می‌کند (بقیه ``None``)."""
    paths: dict = {key: None for key in _OPTIONAL_SLOT_KEYS}
    for key in slots:
        path = directory / f"{key}.xlsx"
        path.write_bytes(b"fake")
        paths[key] = path
    return paths


def _indexed_file_keys(session, kb_id: str) -> set[str]:
    return {row.file_key for row in kb_repo.list_files(session, kb_id)}


def test_two_runs_in_one_project_never_share_files_directories_or_chunk_text(
    isolated_vector_root, db_session, project, user, tmp_path
):
    """دو اجرا با **مجموعه‌ی فایل متفاوت**: هیچ‌کدام فایل/متن دیگری را نمی‌بیند.

    هر اجرا دو فایل متفاوت آپلود می‌کند تا ردیف‌های ``kb_files`` و متن
    ``chunks.jsonl`` هر دو قابل‌تفکیک باشند -- اگر مرزی بین دو KB نشتی داشت،
    حضور کلید فایل یا متن آن یکی در این یکی دیده می‌شود.
    """
    (tmp_path / "run1").mkdir()
    (tmp_path / "run2").mkdir()
    only_one = ("revised_budget", "financial_statements")
    only_two = ("balance_sheet", "credit_approvals")

    join_one = checklist_kb_service.start_indexing(
        user_id=user.id,
        project_id=project.id,
        file_paths=_file_paths_for_slots(tmp_path / "run1", only_one),
    )
    assert _wait_until(lambda: join_one.kb_id is not None)
    join_two = checklist_kb_service.start_indexing(
        user_id=user.id,
        project_id=project.id,
        file_paths=_file_paths_for_slots(tmp_path / "run2", only_two),
    )
    assert _wait_until(lambda: join_two.kb_id is not None)

    assert join_one.kb_id != join_two.kb_id
    with SessionLocal() as session:
        keys_one = _indexed_file_keys(session, join_one.kb_id)
        keys_two = _indexed_file_keys(session, join_two.kb_id)

    # ردیف‌های فایل هر KB فقط کلیدهای خودش را دارند (به‌علاوه‌ی فایل مشترکِ تعریف چک‌لیست).
    assert set(only_one) <= keys_one
    assert not (keys_one & set(only_two))
    assert set(only_two) <= keys_two
    assert not (keys_two & set(only_one))

    # متن chunk ها هم جدا است: هر متادیتا فقط در همان KB پیدا می‌شود.
    records_one = kb_storage.read_chunk_records(user.id, project.id, join_one.kb_id)
    records_two = kb_storage.read_chunk_records(user.id, project.id, join_two.kb_id)
    assert records_one and records_two
    texts_one = {str(record.get("metadata_text")) for record in records_one}
    texts_two = {str(record.get("metadata_text")) for record in records_two}
    assert all(f"metadata:{key}" in texts_one for key in only_one)
    assert not any(f"metadata:{key}" in texts_one for key in only_two)
    assert all(f"metadata:{key}" in texts_two for key in only_two)
    assert not any(f"metadata:{key}" in texts_two for key in only_one)
    # تنها متن مشترک، فایل تعریف چک‌لیست است -- که عمداً در *هر* اجرا ایندکس
    # می‌شود (همان بانک سوالات برای همه‌ی اجراها). جدا از آن، هیچ متنی مشترک نیست.
    assert "metadata:checklist_definition" in texts_one & texts_two
    run_specific_one = {text for text in texts_one if "checklist_definition" not in text}
    run_specific_two = {text for text in texts_two if "checklist_definition" not in text}
    assert run_specific_one.isdisjoint(run_specific_two)


# ---------------------------------------------------------------------------
# سیاست نگهداری در برابر «پایگاه‌دانشِ در حال استفاده» -- آزمون مسابقه‌ای
# ---------------------------------------------------------------------------
def _make_ready_kb_with_directory(
    session, *, project, user_id: int, created_at=None, chunk_id: str = "c1"
):
    """یک KB آماده با پوشه‌ی واقعی روی دیسک و متن chunk قابل‌جست‌وجو.

    شناسه پیش از ساخت ردیف تولید می‌شود تا ``chroma_persist_dir`` به همان شناسه
    اشاره کند و هیچ پوشه‌ی اضافه‌ای («placeholder») در پوشه‌ی پروژه جا نماند.
    """
    from api.db.models import new_kb_id

    kb_id = new_kb_id()
    kb = kb_repo.create(
        session,
        project_id=project.id,
        user_id=user_id,
        embedding_model="fake-embedding-model",
        embedding_device="cpu",
        chroma_collection_name=f"kb-{kb_id}",
        chroma_persist_dir=kb_storage.relative_path_for(user_id, project.id, kb_id),
        kb_id=kb_id,
    )
    if created_at is not None:
        kb.created_at = created_at
        session.commit()
    kb_storage.write_manifest(
        user_id,
        project.id,
        kb_id,
        embedding_model="fake-embedding-model",
        embedding_device="cpu",
        chroma_collection_name=kb.chroma_collection_name,
        created_at="2024-01-01T00:00:00+00:00",
    )
    kb_storage.append_chunks(
        user_id, project.id, kb_id, [_fake_chunk("financial_statements", 0)]
    )
    return kb


def test_retention_never_deletes_the_kb_a_concurrent_ask_is_using(
    tmp_path, db_session, project, user, monkeypatch
):
    """دو ترد هم‌زمان: یکی سیاست نگهداری را اعمال می‌کند، دیگری با همان پروژه می‌پرسد.

    سناریوی خطرِ واقعی همان چیزی است که رفع شد: اشاره‌گر پروژه به پایگاه‌دانشی
    اشاره می‌کند که در ترتیب ``created_at`` جدیدترین **نیست** (ترتیب «آماده‌شدن»
    با ترتیب «ساخته‌شدن» یکی نیست). پیش از رفع، پرس‌وجوی «زائدها» همین
    پایگاه‌دانشِ در حال استفاده را برمی‌گرداند و فقط یک بررسی جداگانه (که می‌توانست
    مقدار قدیمیِ کش‌شده را ببیند) جلوی حذفش را می‌گرفت. حالا خودِ پرس‌وجو اشاره‌گر
    فعلی را کنار می‌گذارد.
    """
    fake_settings = types.SimpleNamespace(
        vector_store_root=tmp_path / "vector_stores",
        embedding_model="fake-embedding-model",
        kb_retention_count=1,  # فقط یکی می‌ماند -- پس فشار حذف واقعی است
    )
    monkeypatch.setattr(kb_storage, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(checklist_kb_service, "get_settings", lambda: fake_settings)

    now = datetime.now(timezone.utc)
    in_use = _make_ready_kb_with_directory(
        db_session, project=project, user_id=user.id, created_at=now - timedelta(minutes=10)
    )
    created_later = _make_ready_kb_with_directory(
        db_session, project=project, user_id=user.id, created_at=now
    )
    # ترتیب «آماده‌شدن» برعکس ترتیب ساخت است: اشاره‌گر نهایتاً روی KB قدیمی‌ترِ ساخت
    # می‌افتد، در حالی که جدیدترینِ ساخت KB دیگری است.
    kb_repo.mark_ready_and_update_project_pointer(db_session, created_later.id)
    kb_repo.mark_ready_and_update_project_pointer(db_session, in_use.id)
    db_session.expire_all()

    in_use_kb_id = in_use.id
    created_later_kb_id = created_later.id
    in_use_dir = kb_storage.path_for(user.id, project.id, in_use_kb_id)

    barrier = threading.Barrier(2, timeout=2)
    lock = threading.Lock()
    deleted: list[str] = []
    resolved: dict[str, str] = {}

    real_delete = kb_storage.delete_kb_directory

    def _recording_delete(user_id_arg, project_id_arg, kb_id_arg):
        with lock:
            deleted.append(kb_id_arg)
        try:
            barrier.wait()
        except threading.BrokenBarrierError:  # pragma: no cover - هم‌زمانی کامل
            pass
        real_delete(user_id_arg, project_id_arg, kb_id_arg)

    monkeypatch.setattr(kb_storage, "delete_kb_directory", _recording_delete)

    class _FakeReadOnlyKB:
        def list_sheets(self):
            return {"financial_statements": ["ترازنامه"]}

    class _FakeAnswer:
        def to_dict(self):
            return {"answer": "پاسخ", "confidence": "high", "sources": [], "route_reasoning": ""}

    def _fake_readonly(user_id_arg, project_id_arg, kb_id_arg, **kwargs):  # noqa: ARG001
        with lock:
            resolved["kb_id"] = kb_id_arg
        try:
            barrier.wait()
        except threading.BrokenBarrierError:  # pragma: no cover - هم‌زمانی کامل
            pass
        return _FakeReadOnlyKB()

    monkeypatch.setattr(rag_ai_adapter, "build_readonly_knowledge_base", _fake_readonly)
    import llm_variable_resolver

    monkeypatch.setattr(llm_variable_resolver, "chat_ask", lambda *a, **k: _FakeAnswer())

    def _run_retention():
        with SessionLocal() as session:
            checklist_kb_service._apply_retention(session, project.id)

    def _run_ask():
        with SessionLocal() as session:
            project_row = session.get(Project, project.id)
            assert project_row is not None
            financial_chatbot_service.ask_with_history(
                session, project_row, "موجودی نقد چقدر است؟"
            )

    threads = [
        threading.Thread(target=_run_retention, name="retention"),
        threading.Thread(target=_run_ask, name="ask"),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert not any(thread.is_alive() for thread in threads), "یک ترد در مهلت مقرر تمام نشد"

    # پرسش واقعاً روی همان پایگاه‌دانشی نشست که اشاره‌گر پروژه بود.
    assert resolved.get("kb_id") == in_use_kb_id
    # و هیچ چیزی حذف نشد: نه پایگاه‌دانشِ در حال استفاده، نه هیچ چیز دیگری.
    assert deleted == []
    assert in_use_dir.exists()
    assert kb_storage.read_chunk_records(user.id, project.id, in_use_kb_id) != []

    with SessionLocal() as fresh:
        assert (
            kb_repo.get_by_id(fresh, in_use_kb_id, user_id=user.id, project_id=project.id)
            is not None
        )
        assert fresh.get(Project, project.id).latest_ready_kb_id == in_use_kb_id

    # --- نیمه‌ی دوم (ضدِ پوچ‌بودن تست) ---------------------------------------
    # با جابه‌جا شدن اشاره‌گر به KB دیگر، همان پایگاه‌دانشِ قبلی واقعاً «زائد»
    # می‌شود و حذف می‌شود -- یعنی مسیر حذف در همین تست سالم و فعال است و
    # نتیجه‌ی بالا از محافظِ اشاره‌گر آمده، نه از یک ابزار خراب.
    kb_repo.mark_ready_and_update_project_pointer(db_session, created_later_kb_id)
    db_session.expire_all()
    with SessionLocal() as session:
        checklist_kb_service._apply_retention(session, project.id)

    assert deleted == [in_use_kb_id], "با جابه‌جا شدن اشاره‌گر، KB قدیمی باید زائد شود"
    assert not in_use_dir.exists()
    with SessionLocal() as fresh:
        assert (
            kb_repo.get_by_id(fresh, in_use_kb_id, user_id=user.id, project_id=project.id) is None
        )
        assert (
            kb_repo.get_by_id(
                fresh, created_later_kb_id, user_id=user.id, project_id=project.id
            )
            is not None
        )


# ---------------------------------------------------------------------------
# یک اجرای چک‌لیست در پروژه‌ی A + یک پرسش از پروژه‌ی B، هم‌زمان
# ---------------------------------------------------------------------------
def test_checklist_run_in_project_a_and_an_ask_in_project_b_never_cross_contaminate(
    isolated_vector_root, db_session, user, project, monkeypatch, tmp_path
):
    """هم‌زمانی بین دو پروژه‌ی همان کاربر: نه KB، نه فایل و نه متن قاطی نمی‌شود."""
    project_b = projects_repo.create(db_session, user.id, "پروژه ب")

    # پروژه‌ی B از قبل یک پایگاه‌دانشِ آماده با متن قابل‌جست‌وجو دارد.
    kb_b = _make_ready_kb_with_directory(
        db_session, project=project_b, user_id=user.id, chunk_id="b-only"
    )
    kb_b_id = kb_b.id
    kb_repo.mark_ready_and_update_project_pointer(db_session, kb_b_id)
    db_session.expire_all()
    kb_b_dir = kb_storage.path_for(user.id, project_b.id, kb_b_id)
    chunks_b_before = kb_storage.read_chunk_records(user.id, project_b.id, kb_b_id)

    (tmp_path / "a").mkdir()
    file_paths_a = _file_paths_for_slots(tmp_path / "a", ("revised_budget", "financial_statements"))

    resolved: dict[str, object] = {}
    lock = threading.Lock()
    barrier = threading.Barrier(2, timeout=5)

    class _FakeReadOnlyKB:
        def list_sheets(self):
            return {"financial_statements": ["ترازنامه"]}

    class _FakeAnswer:
        def to_dict(self):
            return {
                "answer": "پاسخ پروژه ب",
                "confidence": "high",
                "sources": [],
                "route_reasoning": "",
            }

    def _fake_readonly(user_id_arg, project_id_arg, kb_id_arg, **kwargs):  # noqa: ARG001
        with lock:
            resolved["call"] = (user_id_arg, project_id_arg, kb_id_arg)
        try:
            barrier.wait()
        except threading.BrokenBarrierError:  # pragma: no cover
            pass
        return _FakeReadOnlyKB()

    monkeypatch.setattr(rag_ai_adapter, "build_readonly_knowledge_base", _fake_readonly)
    import llm_variable_resolver

    monkeypatch.setattr(llm_variable_resolver, "chat_ask", lambda *a, **k: _FakeAnswer())

    index_result: dict[str, object] = {}

    def _index_project_a():
        join = checklist_kb_service.start_indexing(
            user_id=user.id, project_id=project.id, file_paths=file_paths_a
        )
        index_result["join"] = join
        if _wait_until(lambda: join.kb_id is not None):
            try:
                barrier.wait()
            except threading.BrokenBarrierError:  # pragma: no cover
                pass

    def _ask_project_b():
        with SessionLocal() as session:
            project_row = session.get(Project, project_b.id)
            assert project_row is not None
            financial_chatbot_service.ask_with_history(session, project_row, "موجودی چقدر است؟")

    threads = [
        threading.Thread(target=_index_project_a, name="index-a"),
        threading.Thread(target=_ask_project_b, name="ask-b"),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert not any(thread.is_alive() for thread in threads)

    join_a = index_result["join"]
    assert join_a.kb_id is not None and join_a.kb_id != kb_b_id

    # پرسش فقط از پایگاه‌دانش پروژه‌ی B ساخته شده است.
    assert resolved["call"] == (user.id, project_b.id, kb_b_id)

    with SessionLocal() as session:
        kb_a_keys = _indexed_file_keys(session, join_a.kb_id)
        kb_b_keys = _indexed_file_keys(session, kb_b_id)
        assert kb_repo.get_by_id(
            session, join_a.kb_id, user_id=user.id, project_id=project.id
        ) is not None
        assert kb_repo.get_by_id(
            session, kb_b_id, user_id=user.id, project_id=project_b.id
        ) is not None
    assert "revised_budget" in kb_a_keys
    assert "revised_budget" not in kb_b_keys

    # متن قابل‌جست‌وجوی پروژه‌ی B دست‌نخورده مانده و متن پروژه‌ی A به آن اضافه نشده است.
    chunks_b_after = kb_storage.read_chunk_records(user.id, project_b.id, kb_b_id)
    assert chunks_b_after == chunks_b_before
    assert not any(
        str(record.get("metadata_text", "")).startswith("metadata:revised_budget")
        for record in chunks_b_after
    )
    assert kb_b_dir.exists()
    assert kb_storage.read_chunk_records(user.id, project.id, join_a.kb_id) != []

