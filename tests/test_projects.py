"""تست‌های پروژه‌ها: ساخت، فهرست، جداسازی بین کاربران و حذف کامل (بدون داده‌ی یتیم)."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from api import storage
from api.db.base import SessionLocal
from api.db.models import Project, WorkshopRun, WorkshopSetting, User
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
    assert job_manager.get(job_id) is not None

    auth_client.post(f"/projects/{project.id}/delete", data={"confirm": "delete"})

    assert job_manager.get(job_id) is None
    assert job_manager.list_for_project(project.id) == []


def test_deleting_one_users_project_does_not_touch_another(client, db_session, second_user, auth_client, project):
    """حذف پروژه‌ی یک کاربر هرگز به پروژه‌های کاربر دیگر دست نمی‌زند."""
    foreign = projects_repo.create(db_session, second_user.id, "پروژهٔ کاربر دیگر")

    # کاربر «other» پروژهٔ کاربر اصلی را نمی‌بیند و نمی‌تواند حذفش کند
    client.post("/login", data={"username": "other", "password": "secret123"}, follow_redirects=False)
    assert client.post(f"/projects/{project.id}/delete", data={"confirm": "delete"}).status_code == 404
    assert db_session.get(Project, project.id) is not None
    assert db_session.get(Project, foreign.id) is not None
    assert db_session.scalars(select(User)).all() != []


def test_dashboard_lists_projects_with_run_counts(auth_client, db_session, project):
    _seed_run_with_result(db_session, project)
    page = auth_client.get("/")
    assert "۱ اجرا" in page.text
