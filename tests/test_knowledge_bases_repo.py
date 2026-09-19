"""تست‌های ``api/repositories/knowledge_bases.py``.

این تست‌ها فقط لایه‌ی مخزن را می‌آزمایند: ساخت/فهرست/جداسازی کاربر-پروژه،
قرارداد «هر اجرا یک KB جدید»، اتمیک‌بودن
``mark_ready_and_update_project_pointer`` و صحت سیاست نگهداری
(``list_stale_beyond_retention``). هیچ‌کدام به ``rag_chat_module`` یا فراخوانی
واقعی LLM/embedder نیاز ندارند.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from api.repositories import knowledge_bases as kb_repo
from api.repositories import projects as projects_repo


def _make_kb(db_session, *, project_id, user_id, status="ready"):
    return kb_repo.create(
        db_session,
        project_id=project_id,
        user_id=user_id,
        embedding_model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        embedding_device="cpu",
        chroma_collection_name="kb-test",
        chroma_persist_dir="1/1/fake",
        status=status,
    )


def test_create_makes_a_new_row_with_a_fresh_uuid_every_time(db_session, project, user):
    kb1 = _make_kb(db_session, project_id=project.id, user_id=user.id)
    kb2 = _make_kb(db_session, project_id=project.id, user_id=user.id)

    assert kb1.id != kb2.id
    assert kb1.chroma_collection_name == kb2.chroma_collection_name  # همان مقدار پاس‌داده‌شده
    rows = kb_repo.list_by_project(db_session, project.id)
    assert {row.id for row in rows} == {kb1.id, kb2.id}


def test_list_by_project_orders_newest_first(db_session, project, user):
    kb1 = _make_kb(db_session, project_id=project.id, user_id=user.id)
    kb1.created_at = datetime.now(timezone.utc) - timedelta(days=2)
    kb2 = _make_kb(db_session, project_id=project.id, user_id=user.id)
    kb2.created_at = datetime.now(timezone.utc) - timedelta(days=1)
    kb3 = _make_kb(db_session, project_id=project.id, user_id=user.id)
    kb3.created_at = datetime.now(timezone.utc)
    db_session.commit()

    rows = kb_repo.list_by_project(db_session, project.id)
    assert [row.id for row in rows] == [kb3.id, kb2.id, kb1.id]


def test_get_by_id_requires_matching_user_and_project(db_session, project, user, second_user):
    other_project = projects_repo.create(db_session, second_user.id, "پروژه‌ی دیگر")
    kb = _make_kb(db_session, project_id=project.id, user_id=user.id)

    # صاحب واقعی: پیدا می‌شود
    assert kb_repo.get_by_id(db_session, kb.id, user_id=user.id, project_id=project.id) is not None

    # کاربر دیگر: پیدا نمی‌شود (نه خطای دیگر -- فقط None، مطابق الگوی ۴۰۴)
    assert kb_repo.get_by_id(db_session, kb.id, user_id=second_user.id, project_id=project.id) is None

    # پروژه‌ی دیگر (حتی متعلق به کاربر دیگر): پیدا نمی‌شود
    assert kb_repo.get_by_id(db_session, kb.id, user_id=user.id, project_id=other_project.id) is None

    # شناسه‌ی نامعتبر: پیدا نمی‌شود
    assert kb_repo.get_by_id(db_session, "does-not-exist", user_id=user.id, project_id=project.id) is None


def test_mark_ready_and_update_project_pointer_is_atomic(db_session, project, user):
    kb = _make_kb(db_session, project_id=project.id, user_id=user.id, status="indexing")
    assert project.latest_ready_kb_id is None

    updated = kb_repo.mark_ready_and_update_project_pointer(db_session, kb.id)
    assert updated is not None
    assert updated.status == kb_repo.READY

    db_session.refresh(project)
    assert project.latest_ready_kb_id == kb.id


def test_mark_ready_moves_the_pointer_to_the_newest_ready_kb(db_session, project, user):
    kb1 = _make_kb(db_session, project_id=project.id, user_id=user.id, status="indexing")
    kb_repo.mark_ready_and_update_project_pointer(db_session, kb1.id)

    kb2 = _make_kb(db_session, project_id=project.id, user_id=user.id, status="indexing")
    kb_repo.mark_ready_and_update_project_pointer(db_session, kb2.id)

    db_session.refresh(project)
    assert project.latest_ready_kb_id == kb2.id


def test_update_status_records_failure_without_touching_project_pointer(db_session, project, user):
    kb = _make_kb(db_session, project_id=project.id, user_id=user.id, status="indexing")
    kb_repo.update_status(db_session, kb.id, status=kb_repo.FAILED, error_message="خطای نمونه")

    db_session.refresh(kb)
    db_session.refresh(project)
    assert kb.status == kb_repo.FAILED
    assert kb.error_message == "خطای نمونه"
    assert project.latest_ready_kb_id is None


def test_delete_by_id_clears_project_pointer_when_it_pointed_to_the_deleted_kb(
    db_session, project, user
):
    kb = _make_kb(db_session, project_id=project.id, user_id=user.id, status="indexing")
    kb_repo.mark_ready_and_update_project_pointer(db_session, kb.id)

    assert kb_repo.delete_by_id(db_session, kb.id) is True

    db_session.refresh(project)
    assert project.latest_ready_kb_id is None
    assert kb_repo.get_by_id(db_session, kb.id, user_id=user.id, project_id=project.id) is None


def test_delete_by_id_is_false_for_unknown_id(db_session):
    assert kb_repo.delete_by_id(db_session, "does-not-exist") is False


def test_add_file_and_list_files(db_session, project, user):
    kb = _make_kb(db_session, project_id=project.id, user_id=user.id)
    kb_repo.add_file(
        db_session,
        kb.id,
        file_key="taidiyeh",
        original_filename="تاییدیه.xlsx",
        status="done",
        sheet_count=3,
        chunk_count=12,
    )
    files = kb_repo.list_files(db_session, kb.id)
    assert len(files) == 1
    assert files[0].file_key == "taidiyeh"
    assert files[0].chunk_count == 12


def test_list_stale_beyond_retention_returns_oldest_first_beyond_keep_count(
    db_session, project, user
):
    now = datetime.now(timezone.utc)
    kbs = []
    for i in range(5):
        kb = _make_kb(db_session, project_id=project.id, user_id=user.id, status="ready")
        kb.created_at = now - timedelta(days=5 - i)  # kbs[0] \u0642\u062f\u06cc\u0645\u06cc\u200c\u062a\u0631\u06cc\u0646, kbs[4] \u062c\u062f\u06cc\u062f\u062a\u0631\u06cc\u0646
        kbs.append(kb)
    db_session.commit()

    stale = kb_repo.list_stale_beyond_retention(db_session, project.id, keep_count=3)

    # \u0633\u0647 \u062a\u0627\u06cc \u062c\u062f\u06cc\u062f\u062a\u0631 (kbs[2], kbs[3], kbs[4]) \u0646\u06af\u0647 \u062f\u0627\u0634\u062a\u0647 \u0645\u06cc\u200c\u0634\u0648\u0646\u062f\u061b \u062f\u0648\u062a\u0627\u06cc \u0642\u062f\u06cc\u0645\u06cc\u200c\u062a\u0631 \u0632\u0627\u0626\u062f\u0647 \u0647\u0633\u062a\u0646\u062f
    assert [kb.id for kb in stale] == [kbs[0].id, kbs[1].id]


def test_list_stale_beyond_retention_ignores_indexing_rows(db_session, project, user):
    now = datetime.now(timezone.utc)
    old_indexing = _make_kb(db_session, project_id=project.id, user_id=user.id, status="indexing")
    old_indexing.created_at = now - timedelta(days=10)
    for i in range(3):
        kb = _make_kb(db_session, project_id=project.id, user_id=user.id, status="ready")
        kb.created_at = now - timedelta(days=i)
    db_session.commit()

    stale = kb_repo.list_stale_beyond_retention(db_session, project.id, keep_count=3)
    assert old_indexing.id not in [kb.id for kb in stale]


def test_list_stale_beyond_retention_empty_when_within_limit(db_session, project, user):
    _make_kb(db_session, project_id=project.id, user_id=user.id, status="ready")
    _make_kb(db_session, project_id=project.id, user_id=user.id, status="ready")

    stale = kb_repo.list_stale_beyond_retention(db_session, project.id, keep_count=3)
    assert stale == []


def test_list_stale_beyond_retention_never_lists_the_current_latest_kb(db_session, project, user):
    """اشاره‌گر پروژه حتی وقتی جدیدترینِ ``created_at`` نیست هم محافظت می‌شود.

    ترتیب «آماده‌شدن» می‌تواند با ترتیب «ساخته‌شدن» یکی نباشد (دو اجرای
    هم‌زمان). این تست همان حالت را می‌سازد: KB قدیمی‌ترِ ساخت، دیرتر آماده شده و
    بنابراین ``latest_ready_kb_id`` است. پرس‌وجو باید آن را کنار بگذارد حتی با
    ``keep_count=1``.
    """
    now = datetime.now(timezone.utc)
    older_by_creation = _make_kb(db_session, project_id=project.id, user_id=user.id, status="ready")
    older_by_creation.created_at = now - timedelta(minutes=10)
    newer_by_creation = _make_kb(db_session, project_id=project.id, user_id=user.id, status="ready")
    newer_by_creation.created_at = now
    db_session.commit()

    # ابتدا جدیدترینِ ساخت آماده می‌شود، سپس قدیمی‌ترِ ساخت -- پس اشاره‌گر روی دومی می‌ماند.
    kb_repo.mark_ready_and_update_project_pointer(db_session, newer_by_creation.id)
    kb_repo.mark_ready_and_update_project_pointer(db_session, older_by_creation.id)

    stale = kb_repo.list_stale_beyond_retention(db_session, project.id, keep_count=1)

    # هیچ‌چیز زائد نیست: جدیدترین نگه داشته می‌شود و اشاره‌گر فعلی هم محافظت شده است.
    assert stale == []

    # اما وقتی اشاره‌گر جابه‌جا شود، همان KB دیگر واقعاً زائد است (و حذف می‌شود).
    kb_repo.mark_ready_and_update_project_pointer(db_session, newer_by_creation.id)
    stale = kb_repo.list_stale_beyond_retention(db_session, project.id, keep_count=1)
    assert [kb.id for kb in stale] == [older_by_creation.id]


def test_list_stale_beyond_retention_still_honours_keep_count_without_a_pointer(
    db_session, project, user
):
    """وقتی اشاره‌گر پروژه خالی است، همه‌ی ردیف‌ها مثل قبل شمرده می‌شوند (NOT NULL درست)."""
    now = datetime.now(timezone.utc)
    kbs = []
    for index in range(4):
        kb = _make_kb(db_session, project_id=project.id, user_id=user.id, status="ready")
        kb.created_at = now - timedelta(days=4 - index)
        kbs.append(kb)
    db_session.commit()
    assert project.latest_ready_kb_id is None

    stale = kb_repo.list_stale_beyond_retention(db_session, project.id, keep_count=2)

    # دو ردیفِ قدیمی‌تر زائدند -- یعنی خالی‌بودن اشاره‌گر هیچ ردیفی را از دست نمی‌دهد.
    assert [kb.id for kb in stale] == [kbs[0].id, kbs[1].id]
