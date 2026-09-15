"""Fixtures مشترک برای تست‌های لایه‌ی API (هر دو کارگاه چک‌لیست و خلاصه‌سازی).

این تست‌ها هرگز پایپلاین واقعی (``pipeline.run_full_pipeline`` /
``audit_pipeline.run_audit_summary``) را اجرا نمی‌کنند -- نقطه‌ی شروع ترد
پس‌زمینه (``pipeline.start_checklist_job`` / ``audit_pipeline.start_audit_summary_job``)
با یک نسخه‌ی synchronous و بدون وابستگی خارجی (بدون نیاز به Tesseract،
LibreOffice یا فراخوانی واقعی LLM) جایگزین می‌شود؛ دقیقاً روی همان مرز لایه‌ی
سرویس که در توضیح فاز ۳ خواسته شده است.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.jobs.job_manager import job_manager
from api.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_job_registry():
    """جلوگیری از نشت job بین تست‌ها (job_manager یک singleton سراسری است)."""
    yield
    with job_manager._lock:  # noqa: SLF001 -- صرفاً برای پاک‌سازی state تست
        job_manager._jobs.clear()
