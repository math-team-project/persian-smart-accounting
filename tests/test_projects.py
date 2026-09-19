"""تست‌های پروژه‌ها: ساخت، فهرست، جداسازی بین کاربران و حذف کامل (بدون داده‌ی یتیم)."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from api import storage
from api.db.base import SessionLocal
from api.db.models import KBFile, KnowledgeBase, Project, WorkshopRun, WorkshopSetting, User
from api.repositories import projects as projects_repo
from api.repositories import workshop_runs as runs_repo


def _seed_run_with_result(db_session, project, *, status="done", filename="نتیجه.docx") -> WorkshopRun:
    """یک اجرای تمام‌شده به‌همراه فایل نتیجه‌اش روی دیسک می‌سازد (شبیه‌سازی تاریخچه)."""
    run = runs_repo.create(db_session, project.id, "checklist")
    relative, path = storage.save_result_file(project.id, run.id, filename, b"docx-bytes")
    assert path.exists()
    runs_repo.finish(
        db_session,
        run.id,
        status=status,
        result_summary={"total": 3, "result_filename": filename},
        result_file_path=relative,
    )
    return run


def _seed_ready_kb(db_session, project, *, user_id=None):
    """یک پایگاه‌دانش ``ready`` واقعی با ردیف ``kb_files`` و پوشه‌ی روی‌دیسک می‌سازد.

    عمداً از همان توابع تولید (``kb_repo``/``kb_storage``) استفاده می‌کند تا
    ساختار دیسک دقیقاً همان چیزی باشد که در اجرای واقعی ساخته می‌شود -- نه یک
    شبیه‌سازی دستی. ``manifest.json`` و ``chunks.jsonl`` هر دو نوشته می‌شوند تا
    تست بتواند واقعاً «پوشه خالی/پر» را بسنجد.

    شناسه‌ی پایگاه‌دانش پیش از ساخت ردیف تولید می‌شود تا ``chroma_persist_dir`` و
    مسیر دیسک به همان شناسه اشاره کنند -- دقیقاً همان ترتیبی که
    ``checklist_kb_service.start_indexing`` در اجرای واقعی طی می‌کند.
    """
    from api.db.models import new_kb_id
    from api.repositories import knowledge_bases as kb_repo
    from api.services import kb_storage

    owner = project.user_id if user_id is None else user_id
    kb_id = new_kb_id()
    kb = kb_repo.create(
        db_session,
        project_id=project.id,
        user_id=owner,
        embedding_model="sentence-transformers/test-model",
        embedding_device="cpu",
        chroma_collection_name=f"kb-{kb_id}",
        chroma_persist_dir=kb_storage.relative_path_for(owner, project.id, kb_id),
        kb_id=kb_id,
    )
    kb_storage.write_manifest(
        owner,
        project.id,
        kb_id,
        embedding_model="sentence-transformers/test-model",
        embedding_device="cpu",
        chroma_collection_name=kb.chroma_collection_name,
        created_at="2024-01-01T00:00:00+00:00",
    )
    (kb_storage.path_for(owner, project.id, kb_id) / "chunks.jsonl").write_text(
        '{"chunk_id": "c1"}\n', encoding="utf-8"
    )
    kb_repo.add_file(
        db_session,
        kb.id,
        file_key="financial_statements",
        original_filename="صورت‌های مالی.xlsx",
        status="ok",
        sheet_count=1,
        chunk_count=2,
    )
    kb_repo.mark_ready_and_update_project_pointer(db_session, kb.id)
    return kb


def _project_vector_directory(project) -> Path:
    """پوشه‌ی ``{root}/{user}/{project}`` -- بدون ساختن آن (برخلاف ``path_for``)."""
    from api.services import kb_storage

    return kb_storage.vector_store_root() / str(project.user_id) / str(project.id)


def _kb_vector_directory(project, kb_id: str) -> Path:
    """پوشه‌ی یک پایگاه‌دانش -- بدون ساختن آن.

    توجه: ``kb_storage.path_for`` خودش پوشه را می‌سازد، پس برای «آیا وجود دارد؟»
    هرگز نباید از آن استفاده کرد؛ این کمک‌تابع فقط مسیر را می‌سازد.
    """
    return _project_vector_directory(project) / str(kb_id)


def test_create_project_and_see_it_on_dashboard(auth_client, db_session, user):
    response = auth_client.post("/projects", data={"name": "پروژهٔ الف"}, follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/projects/")

    dashboard = auth_client.get("/")
    assert "پروژهٔ الف" in dashboard.text
    assert "۰ اجرا" in dashboard.text

    project_id = int(location.rsplit("/", 1)[-1])
    assert projects_repo.get_owned(db_session, project_id, user.id) is not None


def test_create_project_requires_name(auth_client, db_session):
    response = auth_client.post("/projects", data={"name": "   "})
    assert response.status_code == 400
    assert "نام پروژه را وارد کنید" in response.text
    assert projects_repo.list_for_user(db_session, 1) == []


def test_project_page_shows_workshops_from_registry(auth_client, project):
    page = auth_client.get(f"/projects/{project.id}")
    assert page.status_code == 200
    # هر دو کارگاه از رجیستری رندر می‌شوند (نه هاردکد در قالب)
    assert "بررسی چک‌لیست حسابرسی مالی" in page.text
    assert "خلاصه‌سازی گزارش حسابرسی" in page.text
    assert f"/projects/{project.id}/checklist" in page.text
    assert f"/projects/{project.id}/audit-summary" in page.text


def test_workshop_page_unknown_slug_returns_404(auth_client, project):
    response = auth_client.get(f"/projects/{project.id}/not-a-workshop")
    assert response.status_code == 404


def test_project_is_invisible_to_other_users(client, db_session, second_user, project):
    client.post("/login", data={"username": "other", "password": "secret123"}, follow_redirects=False)

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert project.name not in dashboard.text

    assert client.get(f"/projects/{project.id}").status_code == 404
    assert client.get(f"/api/projects/{project.id}/jobs").status_code == 404
    assert client.get(f"/projects/{project.id}/checklist").status_code == 404


def test_delete_project_requires_confirmation(auth_client, db_session, project):
    response = auth_client.post(f"/projects/{project.id}/delete", data={})
    assert response.status_code == 400
    assert db_session.get(Project, project.id) is not None


def test_delete_project_removes_rows_and_result_files(auth_client, db_session, project):
    run = _seed_run_with_result(db_session, project)
    other_project = projects_repo.create(db_session, project.user_id, "پروژهٔ دیگر")
    other_run = _seed_run_with_result(db_session, other_project)

    # شناسه‌ها پیش از حذف گرفته می‌شوند (پس از حذف، دسترسی به خودِ شیء ORM خطا می‌دهد).
    project_id = project.id
    other_project_id = other_project.id
    other_run_id = other_run.id

    project_dir = storage.project_dir(project_id)
    assert project_dir.exists()
    result_file = storage.resolve(run.result_file_path)
    assert result_file is not None and result_file.exists()

    response = auth_client.post(
        f"/projects/{project_id}/delete", data={"confirm": "delete"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"

    # حذف در نشست دیگری (درخواست HTTP) انجام شده است؛ برای دیدن واقعیتِ پایگاه‌داده
    # از یک نشست تازه استفاده می‌کنیم (نه نشستی که همان ردیف‌ها را در identity map دارد).
    db_session.expunge_all()
    with SessionLocal() as fresh:
        # --- پایگاه‌داده: نه پروژه، نه اجرا، نه تنظیمات ---
        assert fresh.get(Project, project_id) is None
        assert fresh.scalars(select(WorkshopRun).where(WorkshopRun.project_id == project_id)).all() == []
        assert fresh.scalars(select(WorkshopSetting).where(WorkshopSetting.project_id == project_id)).all() == []

        # --- پروژه‌های دیگر دست‌نخورده می‌مانند ---
        assert fresh.get(Project, other_project_id) is not None
        assert fresh.get(WorkshopRun, other_run_id) is not None

    # --- دیسک: پوشهٔ نتیجه‌های همان پروژه کاملاً پاک شده ---
    assert not project_dir.exists()
    assert not result_file.exists()
    assert storage.resolve(other_run.result_file_path).exists()


def test_delete_project_removes_orphan_files_too(auth_client, db_session, project):
    """حتی فایلی که ردیف پایگاه‌داده ندارد (یتیم) هم با حذف پروژه پاک می‌شود."""
    directory = storage.project_dir(project.id)
    directory.mkdir(parents=True, exist_ok=True)
    orphan = directory / "run-999-orphan.docx"
    orphan.write_bytes(b"orphan")

    auth_client.post(f"/projects/{project.id}/delete", data={"confirm": "delete"})

    assert not directory.exists()
    assert not orphan.exists()


def test_delete_project_removes_encrypted_workshop_settings(auth_client, db_session, project):
    """تنظیمات هوش مصنوعی پروژه -- از جمله کلید رمزشده‌ی API -- کامل پاک می‌شود.

    این همان بند «تعریف انجام‌شده» است: با حذف پروژه نباید هیچ ردیف یتیمی از
    ``workshop_settings`` (و در نتیجه هیچ کلید رمزنگاری‌شده‌ای) باقی بماند.
    """
    from api.services import ai_settings

    plain_key = "sk-delete-with-the-project-please"
    ai_settings.save_ai_settings(
        db_session, project.id, "budget-analysis", api_key=plain_key, model="vendor/sample-model"
    )
    ciphertext = runs_repo.get_setting(db_session, project.id, "budget-analysis").api_key
    assert ciphertext and ciphertext.startswith("fernet:v1:")

    # پروژه‌ی دیگر با کلید خودش باید دست‌نخورده بماند
    keeper = projects_repo.create(db_session, project.user_id, "پروژه‌ای که می‌ماند")
    ai_settings.save_ai_settings(db_session, keeper.id, "budget-analysis", api_key="sk-keeper-key")
    keeper_ciphertext = runs_repo.get_setting(db_session, keeper.id, "budget-analysis").api_key
    assert keeper_ciphertext != ciphertext

    project_id = project.id
    keeper_id = keeper.id
    response = auth_client.post(
        f"/projects/{project_id}/delete", data={"confirm": "delete"}, follow_redirects=False
    )
    assert response.status_code == 303

    db_session.expunge_all()
    with SessionLocal() as fresh:
        # --- پایگاه‌داده: هیچ ردیف تنظیماتی برای پروژه‌ی حذف‌شده نمانده است ---
        assert fresh.scalars(
            select(WorkshopSetting).where(WorkshopSetting.project_id == project_id)
        ).all() == []

        # --- و متن رمزشده‌ی کلید هم دیگر هیچ‌جای پایگاه‌داده نیست ---
        remaining_keys = list(fresh.scalars(select(WorkshopSetting.api_key)))
        assert ciphertext not in remaining_keys

        # --- تنظیمات پروژه‌ی دیگر سالم است ---
        assert fresh.scalars(
            select(WorkshopSetting).where(WorkshopSetting.project_id == keeper_id)
        ).all() != []

    # --- و از مسیر API هم دیگر در دسترس نیست ---
    assert auth_client.get(f"/api/projects/{project_id}/settings").status_code == 404
    assert auth_client.get(f"/projects/{project_id}").status_code == 404
    assert auth_client.get(f"/api/projects/{keeper_id}/settings").status_code == 200


def test_delete_project_leaves_no_related_rows_behind(auth_client, db_session, project):
    """شمارش دقیق ردیف‌های حذف‌شده‌ی هر سه جدول وابسته (پایگاه‌داده + دیسک)."""
    from api.db.models import User
    from api.services import ai_settings

    run_ids = [_seed_run_with_result(db_session, project).id for _ in range(3)]
    runs_repo.create(db_session, project.id, "budget-analysis")
    ai_settings.save_ai_settings(db_session, project.id, "budget-analysis", api_key="sk-x")
    ai_settings.save_ai_settings(db_session, project.id, "checklist", model="checklist-only")

    project_id = project.id
    user_id = project.user_id

    auth_client.post(f"/projects/{project_id}/delete", data={"confirm": "delete"})

    db_session.expunge_all()
    with SessionLocal() as fresh:
        assert fresh.get(Project, project_id) is None
        assert fresh.scalars(select(WorkshopRun).where(WorkshopRun.project_id == project_id)).all() == []
        assert fresh.scalars(
            select(WorkshopSetting).where(WorkshopSetting.project_id == project_id)
        ).all() == []
        # حذف پروژه هرگز نباید خود کاربر را بردارد
        assert fresh.get(User, user_id) is not None
        # هیچ ردیف بی‌صاحبی از این پروژه باقی نمانده است
        assert [run.id for run in fresh.scalars(select(WorkshopRun)).all() if run.id in run_ids] == []


def test_delete_project_also_clears_in_memory_jobs(auth_client, db_session, project, monkeypatch):
    """حذف پروژه نباید job در حافظه‌ی همان پروژه را باقی بگذارد."""
    from api.jobs.job_manager import job_manager

    job_id, job = job_manager.create(lambda: {"status": "idle"}, kind="checklist", project_id=project.id)
    # job را «تمام‌شده» می‌کنیم تا محافظ «پردازش در جریان» جلوی حذف را نگیرد؛
    # هدف این تست فقط پاک‌شدن رجیستری است (خودِ محافظ، تست جداگانه دارد).
    job["status"] = "done"
    assert job_manager.get(job_id) is not None

    response = auth_client.post(
        f"/projects/{project.id}/delete", data={"confirm": "delete"}, follow_redirects=False
    )
    assert response.status_code == 303

    assert job_manager.get(job_id) is None
    assert job_manager.list_for_project(project.id) == []


# ---------------------------------------------------------------------------
# محافظ «پردازش در جریان» (Part A3): حذف پروژه هرگز با یک اجرای در جریان
# مسابقه نمی‌دهد -- رد می‌شود تا آن اجرا تمام شود.
# ---------------------------------------------------------------------------
def test_delete_project_is_refused_while_an_active_job_is_running(auth_client, db_session, project):
    """وجود یک job ناتمام برای همین پروژه ⇒ ۴۰۹ با پیام فارسی، و هیچ‌چیز حذف نمی‌شود."""
    from api.jobs.job_manager import job_manager
    from api.services import project_service

    run = _seed_run_with_result(db_session, project)
    result_file = storage.resolve(run.result_file_path)
    run_id = run.id
    assert result_file is not None and result_file.exists()

    job_id, job = job_manager.create(
        lambda: {"status": "idle"}, kind="checklist", project_id=project.id
    )
    # ``create`` وضعیت را به ``pending`` نرمال می‌کند -- یعنی job «در جریان» است.
    assert job["status"] == "pending"

    response = auth_client.post(
        f"/projects/{project.id}/delete", data={"confirm": "delete"}, follow_redirects=False
    )

    assert response.status_code == 409
    # پیام فارسیِ خودِ سرویس (نه یک متن انگلیسی خام) به کاربر می‌رسد.
    assert project_service.ACTIVE_JOB_BLOCK_MESSAGE_FA in response.text

    # هیچ‌چیز تغییر نکرده است: نه ردیف‌ها، نه فایل نتیجه، نه job در حافظه.
    db_session.expunge_all()
    with SessionLocal() as fresh:
        assert fresh.get(Project, project.id) is not None
        assert fresh.get(WorkshopRun, run_id) is not None
    assert result_file.exists()
    assert job_manager.get(job_id) is not None

    # با پایان پردازش، دیگر دلیلی برای رد وجود ندارد.
    job["status"] = "done"
    response = auth_client.post(
        f"/projects/{project.id}/delete", data={"confirm": "delete"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert job_manager.get(job_id) is None


def test_active_job_of_another_project_does_not_block_deletion(auth_client, db_session, project):
    """job یک پروژه‌ی دیگر هرگز جلوی حذف این پروژه را نمی‌گیرد."""
    from api.jobs.job_manager import job_manager

    other = projects_repo.create(db_session, project.user_id, "پروژه‌ی دیگر")
    other_job_id, _other_job = job_manager.create(
        lambda: {"status": "idle"}, kind="checklist", project_id=other.id
    )

    response = auth_client.post(
        f"/projects/{project.id}/delete", data={"confirm": "delete"}, follow_redirects=False
    )
    assert response.status_code == 303

    db_session.expunge_all()
    with SessionLocal() as fresh:
        assert fresh.get(Project, project.id) is None
        assert fresh.get(Project, other.id) is not None
    # job پروژه‌ی دیگر هم دست‌نخورده می‌ماند.
    assert job_manager.get(other_job_id) is not None


# ---------------------------------------------------------------------------
# آبشار حذف پروژه (Part A1): پایگاه‌دانش + فایل‌ها + گفتگوها + دیسک
# ---------------------------------------------------------------------------
def test_delete_project_removes_knowledge_bases_files_and_vector_store_directories(
    auth_client, db_session, project, user
):
    """حذف پروژه: همه‌ی ردیف‌های KB/kb_files و کل زیرپوشه‌ی دیسک همان پروژه."""
    from api.repositories import knowledge_bases as kb_repo
    from api.services import kb_storage

    kb = _seed_ready_kb(db_session, project)
    kb_id = kb.id
    file_ids = [row.id for row in kb_repo.list_files(db_session, kb_id)]
    kb_directory = _kb_vector_directory(project, kb_id)
    project_directory = _project_vector_directory(project)

    assert file_ids, "پیش‌نیاز تست: حداقل یک ردیف kb_files باید ساخته شده باشد"
    assert kb_directory.exists() and (kb_directory / "manifest.json").exists()
    assert project_directory.exists()

    response = auth_client.post(
        f"/projects/{project.id}/delete", data={"confirm": "delete"}, follow_redirects=False
    )
    assert response.status_code == 303

    db_session.expunge_all()
    with SessionLocal() as fresh:
        assert fresh.get(Project, project.id) is None
        assert fresh.get(KnowledgeBase, kb_id) is None
        assert fresh.scalars(select(KBFile).where(KBFile.id.in_(file_ids))).all() == []

    # پوشه‌ی خود KB و پوشه‌ی والدِ پروژه (که خالی شد) هر دو برداشته می‌شوند.
    assert not kb_directory.exists()
    assert not project_directory.exists()
    # ریشه‌ی ذخیره‌سازی و پوشه‌ی کاربر دست‌نخورده می‌مانند (حذف هرگز از پروژه بالاتر نمی‌رود).
    assert kb_storage.vector_store_root().exists()


def test_delete_project_removes_its_chat_sessions_and_messages(auth_client, db_session, project, user):
    """گفتگوهای همین پروژه **و پیام‌هایشان** هم با حذف پروژه پاک می‌شوند."""
    from api.repositories import chat_messages as messages_repo
    from api.repositories import chat_sessions as chat_repo

    chats = [
        chat_repo.create(db_session, project_id=project.id, user_id=user.id, title=f"گفتگو {index}")
        for index in range(3)
    ]
    chat_ids = [chat.id for chat in chats]
    assert chat_ids
    message_ids = [
        messages_repo.create_user_message(
            db_session,
            session_id=chat.id,
            project_id=project.id,
            user_id=user.id,
            content="پرسش کاربر",
        ).id
        for chat in chats
    ]
    assert message_ids

    response = auth_client.post(
        f"/projects/{project.id}/delete", data={"confirm": "delete"}, follow_redirects=False
    )
    assert response.status_code == 303

    db_session.expunge_all()
    with SessionLocal() as fresh:
        assert fresh.scalars(select(chat_repo.ChatSession).where(
            chat_repo.ChatSession.id.in_(chat_ids)
        )).all() == []
        assert fresh.scalars(select(messages_repo.ChatMessage).where(
            messages_repo.ChatMessage.id.in_(message_ids)
        )).all() == []


def test_deleting_project_a_never_touches_project_b_same_user(auth_client, db_session, project, user):
    """**مهم‌ترین تست این فاز**: دو پروژه‌ی یک کاربر، حذف یکی نباید به دیگری دست بزند.

    هر دو پروژه در همین یک تست ساخته می‌شوند و هر دو پایگاه‌دانش، ردیف فایل،
    گفتگو، پیام و پوشه‌ی روی‌دیسک دارند -- پس هر نشتی (DB یا دیسک) قطعاً دیده می‌شود.
    """
    from api.repositories import chat_messages as messages_repo
    from api.repositories import chat_sessions as chat_repo
    from api.repositories import knowledge_bases as kb_repo

    project_b = projects_repo.create(db_session, user.id, "پروژه‌ای که باید بماند")

    kb_a = _seed_ready_kb(db_session, project)
    kb_b = _seed_ready_kb(db_session, project_b)
    chat_a = chat_repo.create(db_session, project_id=project.id, user_id=user.id, title="گفتگوی الف")
    chat_b = chat_repo.create(db_session, project_id=project_b.id, user_id=user.id, title="گفتگوی ب")
    message_a = messages_repo.create_user_message(
        db_session, session_id=chat_a.id, project_id=project.id, user_id=user.id, content="پرسش الف"
    )
    message_b = messages_repo.create_user_message(
        db_session, session_id=chat_b.id, project_id=project_b.id, user_id=user.id, content="پرسش ب"
    )

    kb_a_id, kb_b_id = kb_a.id, kb_b.id
    chat_a_id, chat_b_id = chat_a.id, chat_b.id
    message_a_id, message_b_id = message_a.id, message_b.id
    project_b_id = project_b.id
    files_b = sorted(row.id for row in kb_repo.list_files(db_session, kb_b.id))
    dir_a = _project_vector_directory(project) / kb_a_id
    dir_b = _project_vector_directory(project_b) / kb_b_id
    assert files_b
    assert dir_a.exists() and (dir_b / "manifest.json").exists()

    response = auth_client.post(
        f"/projects/{project.id}/delete", data={"confirm": "delete"}, follow_redirects=False
    )
    assert response.status_code == 303

    db_session.expunge_all()
    with SessionLocal() as fresh:
        assert fresh.get(Project, project.id) is None
        assert fresh.get(KnowledgeBase, kb_a_id) is None

        # --- پروژه‌ی B کامل دست‌نخورده است ---
        assert fresh.get(Project, project_b_id) is not None
        kb_b_row = fresh.get(KnowledgeBase, kb_b_id)
        assert kb_b_row is not None
        assert kb_b_row.project_id == project_b_id
        assert sorted(
            row.id for row in fresh.scalars(
                select(KBFile).where(KBFile.knowledge_base_id == kb_b_id)
            ).all()
        ) == files_b
        assert fresh.get(chat_repo.ChatSession, chat_b_id) is not None
        assert fresh.get(chat_repo.ChatSession, chat_a_id) is None
        # پیام‌ها هم همین‌طور: پیام پروژه‌ی A رفته، پیام پروژه‌ی B سر جایش است.
        assert fresh.get(messages_repo.ChatMessage, message_b_id) is not None
        assert fresh.get(messages_repo.ChatMessage, message_a_id) is None
        # اشاره‌گر پروژه‌ی B هم به پایگاه‌دانش خودش باقی می‌ماند.
        assert fresh.get(Project, project_b_id).latest_ready_kb_id == kb_b_id

    # --- دیسک: پوشه‌ی A رفته، پوشه‌ی B (و محتوایش) سالم است ---
    assert not dir_a.exists()
    assert dir_b.exists() and (dir_b / "chunks.jsonl").exists()
    assert _project_vector_directory(project_b).exists()


def test_delete_project_succeeds_when_the_kb_directory_is_already_missing(auth_client, db_session, project, user):
    """حالت ناسازگارِ نیمه‌کاره (ردیف هست، پوشه نیست) نباید حذف پروژه را بشکند."""
    from api.services import kb_storage

    kb = _seed_ready_kb(db_session, project)
    kb_id = kb.id
    # شبیه‌سازی یک کرش پیشین: ردیف پایگاه‌داده سر جایش است اما پوشه‌ی دیسک نیست.
    kb_storage.delete_kb_directory(user.id, project.id, kb_id)
    # ``path_for`` خودش پوشه را می‌سازد، پس برای این بررسی از مسیر خام استفاده می‌شود.
    assert not _kb_vector_directory(project, kb_id).exists()

    response = auth_client.post(
        f"/projects/{project.id}/delete", data={"confirm": "delete"}, follow_redirects=False
    )

    assert response.status_code == 303
    db_session.expunge_all()
    with SessionLocal() as fresh:
        assert fresh.get(Project, project.id) is None
        assert fresh.get(KnowledgeBase, kb_id) is None
        assert fresh.scalars(select(KBFile)).all() == []


def test_delete_project_leaves_an_unexpectedly_non_empty_vector_store_directory(
    auth_client, db_session, project, user
):
    """اگر چیزی غیرمنتظره در پوشه‌ی پروژه مانده باشد، پاک نمی‌شود (فقط هشدار)."""
    from api.services import kb_storage

    kb = _seed_ready_kb(db_session, project)
    project_directory = _project_vector_directory(project)
    stray = project_directory / "NOT-A-KB-FILE.txt"
    stray.write_text("do-not-delete-unexpected-content", encoding="utf-8")

    response = auth_client.post(
        f"/projects/{project.id}/delete", data={"confirm": "delete"}, follow_redirects=False
    )

    assert response.status_code == 303
    # پوشه‌ی خود KB و ردیف‌ها پاک شده‌اند...
    assert not (project_directory / kb.id).exists()
    db_session.expunge_all()
    with SessionLocal() as fresh:
        assert fresh.get(KnowledgeBase, kb.id) is None
    # ...اما محتوای ناشناخته‌ی داخل پوشه‌ی پروژه دست‌نخورده مانده است.
    assert stray.exists()
    assert stray.read_text(encoding="utf-8") == "do-not-delete-unexpected-content"



def test_deleting_one_users_project_does_not_touch_another(client, db_session, second_user, auth_client, project):
    """حذف پروژه‌ی یک کاربر هرگز به پروژه‌های کاربر دیگر دست نمی‌زند.

    داده‌ی کاربر دوم عمداً شامل پایگاه‌دانش/گفتگو هم هست تا اگر روزی آبشار حذف
    از مرز «کاربر جاری» رد شد، همین تست بگیردش.
    """
    from api.repositories import chat_sessions as chat_repo

    foreign = projects_repo.create(db_session, second_user.id, "پروژهٔ کاربر دیگر")
    foreign_kb = _seed_ready_kb(db_session, foreign)
    foreign_chat = chat_repo.create(
        db_session, project_id=foreign.id, user_id=second_user.id, title="گفتگوی کاربر دیگر"
    )
    foreign_dir = _project_vector_directory(foreign) / foreign_kb.id
    assert foreign_dir.exists()

    # کاربر «other» پروژهٔ کاربر اصلی را نمی‌بیند و نمی‌تواند حذفش کند
    client.post("/login", data={"username": "other", "password": "secret123"}, follow_redirects=False)
    assert client.post(f"/projects/{project.id}/delete", data={"confirm": "delete"}).status_code == 404
    assert db_session.get(Project, project.id) is not None
    assert db_session.get(Project, foreign.id) is not None
    assert db_session.scalars(select(User)).all() != []

    # داده‌ی کاربر دوم دست‌نخورده مانده است (پایگاه‌داده و دیسک).
    db_session.expunge_all()
    with SessionLocal() as fresh:
        assert fresh.get(KnowledgeBase, foreign_kb.id) is not None
        assert fresh.get(chat_repo.ChatSession, foreign_chat.id) is not None
    assert foreign_dir.exists()


def _iter_route_endpoints(routes):
    """همه‌ی ``(route, endpoint)`` های واقعی یک اپ FastAPI را برمی‌گرداند.

    نسخه‌های تازه‌ی FastAPI روترهای ``include_router`` شده را در یک شیء میانی
    (``_IncludedRouter``) نگه می‌دارند، بنابراین ``app.routes`` دیگر فهرست تختِ
    مسیرها نیست. این تابع هم شکل تخت (نسخه‌های قدیمی) و هم شکل تودرتو را پوشش
    می‌دهد تا تست به نسخه‌ی FastAPI وابسته نباشد.
    """
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _iter_route_endpoints(inner.routes)
            continue
        endpoint = getattr(route, "endpoint", None)
        if endpoint is not None:
            yield route, endpoint


def test_the_three_project_wide_deletion_helpers_are_not_reachable_from_any_route():
    """سه تابع آبشار حذف پروژه هیچ‌گاه endpoint نمی‌شوند.

    یک پروژه فقط و فقط از یک مسیر قابل پاک‌شدن است: ``POST
    /projects/{project_id}/delete``. توابع «کل پروژه» باید صرفاً از داخل
    ``project_service.delete_project`` صدا زده شوند؛ اگر کسی روزی یکی از آن‌ها را
    به‌عنوان یک مسیر جدا ثبت کند، این تست می‌شکند.
    """
    from api.main import app
    from api.repositories import chat_sessions as chat_sessions_module
    from api.repositories import knowledge_bases as knowledge_bases_module
    from api.services import kb_storage as kb_storage_module
    from api.services import project_service

    # ۱) نام‌های صریح وجود دارند (تا تغییر نام بی‌سروصدا این تست را بی‌اثر نکند).
    helpers = {
        "delete_all_chat_sessions_for_project": chat_sessions_module.delete_all_chat_sessions_for_project,
        "delete_vector_store_directories_for_project": kb_storage_module.delete_vector_store_directories_for_project,
        "delete_all_knowledge_bases_for_project": knowledge_bases_module.delete_all_knowledge_bases_for_project,
    }
    for name, helper in helpers.items():
        assert callable(helper), name

    # ۲) هیچ‌کدام از این سه تابع endpoint هیچ مسیری نیستند.
    route_endpoint_names = {
        getattr(endpoint, "__name__", "") for _route, endpoint in _iter_route_endpoints(app.routes)
    }
    assert not (set(helpers) & route_endpoint_names)

    # ۳) تنها مسیری که نامش «حذف» دارد و به پروژه مربوط است، همان یک مسیر است --
    #    یعنی هیچ مسیر پنهانی برای «حذف دسته‌ای پایگاه‌دانش/گفتگو» وجود ندارد.
    paths = app.openapi()["paths"]
    project_delete_routes = sorted(
        path for path in paths if "delete" in path and "projects" in path
    )
    assert project_delete_routes == ["/projects/{project_id}/delete"]
    assert set(paths["/projects/{project_id}/delete"]) == {"post"}
    # مسیر «حذف یک گفتگوی تکی» هنوز هست (عملیات باریک و ایمن، نه آبشار پروژه).
    assert "delete" in paths["/api/projects/{project_id}/financial_chatbot/sessions/{session_id}"]

    # ۴) و خودِ سرویس واقعاً همان سه تابع را صدا می‌زند (پیوند، نه فقط ادعا).
    import inspect

    source = inspect.getsource(project_service.delete_project)
    for name in helpers:
        assert name in source, f"delete_project دیگر {name} را صدا نمی‌زند"



def test_dashboard_lists_projects_with_run_counts(auth_client, db_session, project):
    _seed_run_with_result(db_session, project)
    page = auth_client.get("/")
    assert "۱ اجرا" in page.text
