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
from pathlib import Path

import pytest

from api.config import get_settings
from api.db.base import SessionLocal
from api.db.models import Project
from api.repositories import knowledge_bases as kb_repo
from api.services import checklist_kb_service, kb_storage, rag_ai_adapter

pytestmark = pytest.mark.usefixtures("_fresh_state")


class _FakeIndexReport:
    def __init__(self, *, files_indexed, total_chunks, errors=None):
        self.files_indexed = files_indexed
        self.total_chunks = total_chunks
        self.total_files = len(files_indexed)
        self.errors = errors or []


class _FakeKnowledgeBase:
    """جایگزین ``llm_variable_resolver.knowledge_base.KnowledgeBase``."""

    fail_files = frozenset()
    empty = False

    def __init__(self, embedder, vector_store_factory, segmenter=None):
        self.embedder = embedder
        self.vector_store_factory = vector_store_factory

    def index_files(self, file_registry, sheet_descriptions=None):
        if type(self).empty:
            return _FakeIndexReport(files_indexed={}, total_chunks=0, errors=["nothing to index"])
        files_indexed = {}
        total_chunks = 0
        for key in file_registry:
            if key in type(self).fail_files:
                files_indexed[key] = {"status": "error", "error": "boom"}
                continue
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
