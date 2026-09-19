"""Fixtures مشترک برای تست‌های لایه‌ی API (داشبورد، پروژه‌ها، هر دو کارگاه).

این تست‌ها هرگز پایپلاین واقعی (``pipeline.run_full_pipeline`` /
``audit_pipeline.run_audit_summary``) را اجرا نمی‌کنند -- نقطه‌ی شروع ترد
پس‌زمینه (``pipeline.start_checklist_job`` / ``audit_pipeline.start_audit_summary_job``)
با یک نسخه‌ی synchronous و بدون وابستگی خارجی (بدون نیاز به Tesseract،
LibreOffice یا فراخوانی واقعی LLM) جایگزین می‌شود؛ دقیقاً روی همان مرز لایه‌ی
سرویس.

پایگاه‌داده و پوشه‌ی نتیجه‌ها هم به یک پوشه‌ی موقت جداگانه در همان ابتدای
ایمپورت هدایت می‌شوند (پیش از ساخت engine در ``api/db/base.py``) تا هیچ تستی به
داده‌های واقعی پروژه دست نزند.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# --- پیکربندی محیط تست، *پیش از* ایمپورت api.main (engine در زمان ایمپورت
# ساخته می‌شود و تنظیمات lru_cache است) --------------------------------------
TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="psa-tests-"))
os.environ["PSA_DB_URL"] = f"sqlite:///{(TEST_DATA_DIR / 'test.db').as_posix()}"
os.environ["PSA_RESULTS_DIR"] = str(TEST_DATA_DIR / "results")
# ریشه‌ی فایل‌های روی-دیسک پایگاه‌دانش (Chroma/manifest/chunks.jsonl) هم به پوشه‌ی
# موقت هدایت می‌شود. بدون این خط، هر تستی که مسیر یک پایگاه‌دانش را می‌سازد یا
# پاک می‌کند روی ``data/vector_stores`` واقعیِ پروژه کار می‌کرد -- دقیقاً همان
# چیزی که این فایل قصد دارد مانعش شود.
os.environ["PSA_VECTOR_STORE_ROOT"] = str(TEST_DATA_DIR / "vector_stores")
os.environ["PSA_SECRET_KEY"] = "test-secret-key"
os.environ["PSA_MAX_UPLOAD_MB"] = "5"

# جلوگیری کامل از خوانده‌شدن .env واقعی پروژه در تست‌ها.
#
# ``api/main.py`` در زمان ایمپورت ``load_dotenv()`` را صدا می‌زند و هر کلیدی که
# از قبل در محیط نباشد را از ``.env`` می‌خواند -- یعنی بدون این محافظ، «کلید
# پیش‌فرض سامانه» در تست‌ها به کلید واقعی توسعه‌دهنده وابسته می‌شد (و حتی ممکن
# بود یک فراخوانی شبکه‌ای واقعی رخ دهد). دو کار انجام می‌شود:
#
#   ۱) کلیدهای LLM از محیط حذف می‌شوند (اگر در شل توسعه‌دهنده export شده باشند)،
#   ۲) ``load_dotenv`` بی‌اثر می‌شود تا آن‌ها را از ``.env`` برنگرداند.
#
# نتیجه: پیش‌فرض سامانه در تست‌ها قطعاً «بدون کلید» است (``None``، نه رشته‌ی خالی).
for _llm_key_variable in (
    "AUDIT_REPORT_LLM_API_KEY",
    "API_KEY_OPENROUTER",
    "B_AI_API_KEY",
    "API_KEY_B_AI",
):
    os.environ.pop(_llm_key_variable, None)

import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *args, **kwargs: False  # noqa: ARG005

from fastapi.testclient import TestClient  # noqa: E402

from api import storage  # noqa: E402
from api.auth import passwords  # noqa: E402
from api.db.base import Base, SessionLocal, engine, init_db  # noqa: E402
from api.jobs.job_manager import job_manager  # noqa: E402
from api.main import app  # noqa: E402
from api.repositories import projects as projects_repo  # noqa: E402
from api.repositories import users as users_repo  # noqa: E402

USERNAME = "tester"
PASSWORD = "secret123"


def pytest_sessionfinish(session, exitstatus):  # pragma: no cover - پاک‌سازی پس از اجرا
    shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _fresh_state():
    """پایگاه‌داده و رجیستری job ها بین تست‌ها تازه می‌شوند (هر دو singleton سراسری‌اند).

    پوشه‌های موقت کاری که تست‌ها می‌سازند هم پاک می‌شوند: تابع واقعی شروع پردازش
    (``pipeline.start_checklist_job``) در تست‌ها با نسخه‌ی synchronous جایگزین
    شده و بنابراین ``finally`` خودش (که workdir را پاک می‌کند) اجرا نمی‌شود.
    """
    created_before = _workdir_snapshot()
    init_db()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    shutil.rmtree(storage.results_root(), ignore_errors=True)
    # پایگاه‌دانش‌های روی-دیسک هم بین تست‌ها پاک می‌شوند تا یک تست نتواند روی
    # نتیجه‌ی تست دیگری اثر بگذارد (همه زیر همان پوشه‌ی موقت‌اند).
    shutil.rmtree(_vector_store_root(), ignore_errors=True)
    with job_manager._lock:  # noqa: SLF001 -- فقط برای پاک‌سازی state در تست
        job_manager._jobs.clear()
    yield
    for path in _workdir_snapshot() - created_before:
        shutil.rmtree(path, ignore_errors=True)


def _vector_store_root() -> Path:
    """ریشه‌ی پوشه‌ی موقت پایگاه‌دانش‌های تست (از همان تنظیمات اپ خوانده می‌شود)."""
    from api.config import get_settings

    return Path(get_settings().vector_store_root)


def _workdir_snapshot() -> set[str]:
    temp_root = Path(tempfile.gettempdir())
    return {str(p) for p in temp_root.glob("psa_dashboard_*")} | {
        str(p) for p in temp_root.glob("psa_audit_summary_*")
    } | {str(p) for p in temp_root.glob("psa_budget_*")}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def db_session():
    with SessionLocal() as session:
        yield session


@pytest.fixture()
def user(db_session):
    return users_repo.create(db_session, USERNAME, passwords.hash_password(PASSWORD))


@pytest.fixture()
def second_user(db_session):
    return users_repo.create(db_session, "other", passwords.hash_password(PASSWORD))


@pytest.fixture()
def auth_client(client, user) -> TestClient:
    """کلاینت واردشده به حساب ``user``."""
    response = client.post(
        "/login",
        data={"username": USERNAME, "password": PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303, "ورود در fixture ناموفق بود"
    return client


@pytest.fixture()
def project(db_session, user):
    return projects_repo.create(db_session, user.id, "پروژهٔ تست")


@pytest.fixture()
def wait_for_run():
    """انتظار برای نهایی‌شدن یک اجرا (ثبت ردیف تاریخچه در ترد نگهبان job).

    مستقیماً ردیف پایگاه‌داده را می‌خواند -- نه پاسخ API را -- چون وضعیت زنده‌ی
    job در حافظه پیش از ثبت تاریخچه هم ``done`` می‌شود و تست باید لحظه‌ای را ببیند
    که نتیجه واقعاً ذخیره شده است.
    """
    from api.db.models import WorkshopRun

    def _wait(project_id: int, run_id: int, timeout: float = 10.0) -> dict:
        deadline = time.time() + timeout
        last_status = None
        while time.time() < deadline:
            with SessionLocal() as session:
                run = session.get(WorkshopRun, run_id)
                if run is not None:
                    last_status = run.status
                    if run.status in ("done", "error"):
                        summary = run.result_summary or {}
                        return {
                            "run_id": run.id,
                            "project_id": run.project_id,
                            "workshop": run.workshop_type,
                            "status": run.status,
                            "error": summary.get("error"),
                            "result_summary": summary,
                            "result_file_path": run.result_file_path,
                            "download_ready": bool(run.result_file_path),
                        }
            time.sleep(0.05)
        raise AssertionError(f"اجرای {run_id} در مهلت مقرر نهایی نشد؛ آخرین وضعیت: {last_status}")

    return _wait
