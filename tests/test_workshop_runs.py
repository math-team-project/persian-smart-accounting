"""تست‌های جریان کامل هر کارگاه داخل یک پروژه + ثبت تاریخچه (فقط نتیجه، بدون ورودی).

دقیقاً مثل تست‌های فاز قبل، ``pipeline.start_checklist_job`` و
``audit_pipeline.start_audit_summary_job`` (تنها نقطه‌هایی که ترد پردازش سنگین را
راه می‌اندازند) با نسخه‌های synchronous و بدون وابستگی خارجی جایگزین می‌شوند.
"""
from __future__ import annotations

import io
import json

from sqlalchemy import select

from api import storage
from api.db.models import WorkshopRun
from api.repositories import workshop_runs as runs_repo


def _fake_xlsx_upload(name: str) -> tuple[str, tuple[str, io.BytesIO, str]]:
    """فایل xlsx مصنوعی -- ``pipeline.save_uploaded_file`` برای پسوند xlsx صرفاً
    بایت‌های خام را روی دیسک می‌نویسد (بدون parse کردن)."""
    return (
        name,
        (
            "dummy.xlsx",
            io.BytesIO(b"not a real xlsx, but bytes are enough"),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
    )


def _required_files() -> dict:
    return dict(
        [
            _fake_xlsx_upload("revised_budget"),
            _fake_xlsx_upload("financial_statements"),
            _fake_xlsx_upload("balance_sheet"),
        ]
    )


def _stub_checklist_kb_indexing(monkeypatch):
    """جلوگیری از اجرای واقعی ایندکس‌سازی پایگاه‌دانش (embedder/LLM واقعی) در این
    تست‌ها -- این فایل فقط جریان تاریخچه/نتیجه را می‌آزماید، نه پایگاه‌دانش را
    (آن، موضوع ``tests/test_checklist_kb_service.py`` است).
    """
    from api.services import checklist_kb_service

    class _FakeJoin:
        def resolve_false_questions(self, false_questions, checklist_results):
            return false_questions

    monkeypatch.setattr(
        checklist_kb_service, "start_indexing", lambda **kwargs: _FakeJoin()
    )


def _make_fake_start_checklist_job(monkeypatch, *, status: str = "done", error: str | None = None):
    import pipeline

    _stub_checklist_kb_indexing(monkeypatch)

    def _fake(job, file_paths, workdir, audit_report_path=None, entity_name=None, **kwargs):
        job["stage"] = "پردازش با موفقیت به اتمام رسید" if status == "done" else "پردازش با خطا متوقف شد"
        job["logs"] = ["در حال شروع پردازش...", "استخراج فایل‌های ورودی..."]
        if status == "error":
            job["status"] = "error"
            job["error"] = error or "خطای نمونه در پردازش"
            return

        job["status"] = "done"
        job["result"] = {
            "summary": {
                "total": 4,
                "true_count": 2,
                "false_count": 1,
                "error_count": 0,
                "manual_count": 1,
                "compliance_rate": 50.0,
            },
            "warnings": ["یک هشدار نمونه"],
            "checklist_results": [
                {
                    "question_id": "Q1",
                    "question_text": "آیا بودجه اصلاحیه مطابق قانون است؟",
                    "question_purpose": "بررسی تطابق بودجه",
                    "is_evaluable": True,
                    "status": "FALSE",
                    "message": "عدم تطابق یافت شد.",
                    "evaluation_condition": "A == B",
                    "condition_breakdown": [{"condition": "A == B", "result": "FAILED"}],
                    "extracted_data": [{"متغیر": "A", "مقدار استخراج‌شده": "100"}],
                }
            ],
            "committee_report": {
                "docx_bytes": b"fake docx bytes",
                "docx_filename": "گزارش_کمیسیون.docx",
                "used_audit_report_text": False,
            },
            "elapsed_seconds": 1.23,
        }

    monkeypatch.setattr(pipeline, "start_checklist_job", _fake)


def _fake_pdf_upload() -> dict:
    return {
        "audit_report": (
            "گزارش.pdf",
            io.BytesIO(b"%PDF-1.4 fake pdf bytes are enough for this test"),
            "application/pdf",
        )
    }


def _make_fake_start_audit_summary_job(monkeypatch, *, status: str = "done", error: str | None = None):
    import audit_pipeline

    def _fake(job, input_path, workdir, **kwargs):
        job["stage"] = "[4/4] پردازش با موفقیت به اتمام رسید" if status == "done" else "پردازش با خطا متوقف شد"
        job["logs"] = ["در حال شروع پردازش...", "[1/4] استخراج متن گزارش..."]
        if status == "error":
            job["status"] = "error"
            job["error"] = error or "خطای نمونه در خلاصه‌سازی"
            return

        job["status"] = "done"
        job["result"] = {
            "docx_bytes": b"fake summary docx bytes",
            "docx_filename": "خلاصه_گزارش_حسابرسی.docx",
            "warnings": [],
            "elapsed_seconds": 2.5,
            "source_filename": input_path.name,
            "summary_markdown": "# خلاصه گزارش\n\n- مورد مهم اول\n- مورد مهم دوم\n",
        }

    monkeypatch.setattr(audit_pipeline, "start_audit_summary_job", _fake)


# ---------------------------------------------------------------------------
# چک‌لیست
# ---------------------------------------------------------------------------
def test_checklist_run_writes_history_with_result_file(auth_client, db_session, project, monkeypatch, wait_for_run):
    _make_fake_start_checklist_job(monkeypatch)

    response = auth_client.post(
        f"/api/projects/{project.id}/checklist/jobs",
        data={"entity_name": "شرکت تست"},
        files=_required_files(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"]
    run_id = body["run_id"]

    # وضعیت زنده
    status = auth_client.get(f"/api/projects/{project.id}/checklist/jobs/{body['job_id']}").json()
    assert status["status"] == "done"
    assert status["progress"] == 100
    assert status["run_id"] == run_id
    assert status["report_ready"] is True

    # نتایج
    results = auth_client.get(f"/api/projects/{project.id}/checklist/jobs/{body['job_id']}/results").json()
    assert results["summary"]["false_count"] == 1
    assert results["items"][0]["question_id"] == "Q1"

    # تاریخچه: ردیف تمام‌شده + فایل نتیجه روی دیسک
    job = wait_for_run(project.id, run_id)
    assert job["status"] == "done"
    assert job["download_ready"] is True

    run = db_session.get(WorkshopRun, run_id)
    db_session.refresh(run)
    assert run.status == "done"
    assert run.finished_at is not None
    assert run.result_summary["false_count"] == 1
    assert run.result_summary["entity_name"] == "شرکت تست"
    assert run.result_file_path and storage.resolve(run.result_file_path).exists()

    # دانلود نتیجه از مسیر «اجرا»
    download = auth_client.get(f"/api/projects/{project.id}/checklist/runs/{run_id}/download")
    assert download.status_code == 200
    assert download.content == b"fake docx bytes"
    assert "attachment" in download.headers["content-disposition"]


def test_checklist_history_row_never_stores_input_files(auth_client, db_session, project, monkeypatch, wait_for_run):
    """تنها نتیجه ذخیره می‌شود: هیچ مسیر/محتوای فایل ورودی در پایگاه‌داده نمی‌ماند."""
    _make_fake_start_checklist_job(monkeypatch)

    response = auth_client.post(
        f"/api/projects/{project.id}/checklist/jobs",
        data={},
        files=_required_files(),
    )
    run_id = response.json()["run_id"]
    wait_for_run(project.id, run_id)

    run = db_session.get(WorkshopRun, run_id)
    db_session.refresh(run)
    stored = json.dumps(run.result_summary, ensure_ascii=False)

    for slot_key in ("revised_budget", "financial_statements", "balance_sheet"):
        assert slot_key not in stored
    assert "dummy.xlsx" not in stored
    # مسیر ذخیره‌شده فقط فایل نتیجه است و بیرون از پوشهٔ نتیجه‌ها چیزی ذخیره نشده
    assert run.result_file_path.startswith(f"project-{project.id}/")
    assert storage.resolve(run.result_file_path).name.endswith(".docx")


def test_checklist_error_still_recorded_in_history(auth_client, db_session, project, monkeypatch, wait_for_run):
    _make_fake_start_checklist_job(monkeypatch, status="error", error="خطای شبیه‌سازی‌شده")

    response = auth_client.post(
        f"/api/projects/{project.id}/checklist/jobs", data={}, files=_required_files()
    )
    run_id = response.json()["run_id"]
    job_id = response.json()["job_id"]

    status = auth_client.get(f"/api/projects/{project.id}/checklist/jobs/{job_id}").json()
    assert status["status"] == "error"
    assert status["error"] == "خطای شبیه‌سازی‌شده"

    job = wait_for_run(project.id, run_id)
    assert job["status"] == "error"
    assert "شبیه‌سازی‌شده" in job["error"]

    run = db_session.get(WorkshopRun, run_id)
    db_session.refresh(run)
    assert run.status == "error"
    assert run.result_summary["error"] == "خطای شبیه‌سازی‌شده"
    assert run.result_file_path is None


def test_checklist_validation_error_does_not_leave_running_row(auth_client, db_session, project):
    response = auth_client.post(f"/api/projects/{project.id}/checklist/jobs", data={}, files={})
    assert response.status_code == 400
    assert "الزامی" in response.json()["detail"]

    runs = db_session.scalars(select(WorkshopRun).where(WorkshopRun.project_id == project.id)).all()
    assert len(runs) == 1
    assert runs[0].status == "error"  # ردیف «در جریان» سرگردان باقی نمی‌ماند


def test_checklist_invalid_extension_returns_400(auth_client, project):
    files = _required_files()
    files["revised_budget"] = ("budget.txt", io.BytesIO(b"hello"), "text/plain")
    response = auth_client.post(f"/api/projects/{project.id}/checklist/jobs", data={}, files=files)
    assert response.status_code == 400
    assert "فرمت" in response.json()["detail"]


def test_checklist_job_is_scoped_to_its_project(auth_client, db_session, project, monkeypatch):
    _make_fake_start_checklist_job(monkeypatch)
    response = auth_client.post(
        f"/api/projects/{project.id}/checklist/jobs", data={}, files=_required_files()
    )
    job_id = response.json()["job_id"]

    from api.repositories import projects as projects_repo

    other = projects_repo.create(db_session, project.user_id, "پروژهٔ دوم")

    # همان job از مسیر پروژهٔ دیگر دیده نمی‌شود
    assert auth_client.get(f"/api/projects/{other.id}/checklist/jobs/{job_id}").status_code == 404


# ---------------------------------------------------------------------------
# خلاصه‌سازی گزارش حسابرسی
# ---------------------------------------------------------------------------
def test_summary_run_writes_history_with_result_file(auth_client, db_session, project, monkeypatch, wait_for_run):
    _make_fake_start_audit_summary_job(monkeypatch)

    response = auth_client.post(
        f"/api/projects/{project.id}/audit-summary/jobs",
        data={"organization": "شرکت تست", "meeting_context": "جلسهٔ هیئت‌مدیره"},
        files=_fake_pdf_upload(),
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    run_id = response.json()["run_id"]

    status = auth_client.get(f"/api/projects/{project.id}/audit-summary/jobs/{job_id}").json()
    assert status["status"] == "done"
    assert status["summary_ready"] is True

    results = auth_client.get(f"/api/projects/{project.id}/audit-summary/jobs/{job_id}/results").json()
    assert "مورد مهم اول" in results["summary_html"]
    assert results["source_filename"] == "گزارش.pdf"

    wait_for_run(project.id, run_id)
    run = db_session.get(WorkshopRun, run_id)
    db_session.refresh(run)
    assert run.status == "done"
    assert run.result_summary["source_filename"] == "گزارش.pdf"
    assert run.result_summary["summary_markdown"].startswith("# خلاصه گزارش")
    assert run.result_summary["organization"] == "شرکت تست"
    assert storage.resolve(run.result_file_path).exists()

    download = auth_client.get(f"/api/projects/{project.id}/audit-summary/runs/{run_id}/download")
    assert download.status_code == 200
    assert download.content == b"fake summary docx bytes"


def test_summary_missing_file_returns_400(auth_client, project):
    response = auth_client.post(f"/api/projects/{project.id}/audit-summary/jobs", data={}, files={})
    assert response.status_code == 400
    assert "بارگذاری" in response.json()["detail"]


# ---------------------------------------------------------------------------
# تاریخچه‌ی پروژه
# ---------------------------------------------------------------------------
def test_history_fragment_lists_runs_with_download_link(auth_client, db_session, project, monkeypatch, wait_for_run):
    _make_fake_start_checklist_job(monkeypatch)
    response = auth_client.post(
        f"/api/projects/{project.id}/checklist/jobs", data={}, files=_required_files()
    )
    run_id = response.json()["run_id"]
    wait_for_run(project.id, run_id)

    history = auth_client.get(f"/projects/{project.id}/history")
    assert history.status_code == 200
    assert "بررسی چک‌لیست حسابرسی مالی" in history.text
    assert f"/api/projects/{project.id}/checklist/runs/{run_id}/download" in history.text
    assert "تکمیل‌شده" in history.text


def test_project_page_renders_history_without_javascript(auth_client, db_session, project, monkeypatch, wait_for_run):
    """تاریخچه باید در همان HTML اولیه باشد، نه فقط پس از تازه‌سازی با جاوااسکریپت."""
    _make_fake_start_checklist_job(monkeypatch)
    response = auth_client.post(
        f"/api/projects/{project.id}/checklist/jobs", data={}, files=_required_files()
    )
    run_id = response.json()["run_id"]
    wait_for_run(project.id, run_id)

    page = auth_client.get(f"/projects/{project.id}")
    assert page.status_code == 200
    assert f"/api/projects/{project.id}/checklist/runs/{run_id}/download" in page.text
    assert "هنوز اجرایی در این پروژه ثبت نشده است" not in page.text


def test_history_shows_error_runs(auth_client, db_session, project, monkeypatch, wait_for_run):
    _make_fake_start_audit_summary_job(monkeypatch, status="error", error="خطای نمونه در خلاصه‌سازی")
    response = auth_client.post(
        f"/api/projects/{project.id}/audit-summary/jobs", data={}, files=_fake_pdf_upload()
    )
    wait_for_run(project.id, response.json()["run_id"])

    history = auth_client.get(f"/projects/{project.id}/history")
    assert "ناموفق" in history.text
    assert "خطای نمونه در خلاصه‌سازی" in history.text


def test_run_download_unknown_run_returns_404(auth_client, project):
    response = auth_client.get(f"/api/projects/{project.id}/checklist/runs/424242/download")
    assert response.status_code == 404


def test_panel_reports_runs_of_both_workshops(auth_client, db_session, project):
    _run = runs_repo.create(db_session, project.id, "checklist")
    _run2 = runs_repo.create(db_session, project.id, "audit-summary")

    panel = auth_client.get(f"/api/projects/{project.id}/jobs").json()
    workshops = {item["workshop"] for item in panel["jobs"]}
    assert workshops == {"checklist", "audit-summary"}
    assert all(item["download_ready"] is False for item in panel["jobs"])

def test_checklist_run_without_report_shows_report_error(auth_client, db_session, project, monkeypatch, wait_for_run):
    """اگر تولید گزارش کمیسیون شکست بخورد، اجرا موفق می‌ماند اما تاریخچه باید علت را نشان دهد."""
    import pipeline

    _stub_checklist_kb_indexing(monkeypatch)

    def _fake(job, file_paths, workdir, audit_report_path=None, entity_name=None, **kwargs):
        job["status"] = "done"
        job["result"] = {
            "summary": {
                "total": 2,
                "true_count": 2,
                "false_count": 0,
                "error_count": 0,
                "manual_count": 0,
                "compliance_rate": 100.0,
            },
            "warnings": [],
            "checklist_results": [],
            "committee_report": {"error": "تولید گزارش کمیسیون ناموفق بود: سهمیهٔ سرویس هوش مصنوعی تمام شد."},
            "elapsed_seconds": 0.5,
        }

    monkeypatch.setattr(pipeline, "start_checklist_job", _fake)

    response = auth_client.post(
        f"/api/projects/{project.id}/checklist/jobs", data={}, files=_required_files()
    )
    run_id = response.json()["run_id"]
    job = wait_for_run(project.id, run_id)

    assert job["status"] == "done"
    assert job["result_file_path"] is None
    assert "گزارش کمیسیون" in job["result_summary"]["report_error"]

    history = auth_client.get(f"/projects/{project.id}/history")
    assert "تولید فایل گزارش کمیسیون ناموفق بود" in history.text
    assert "گزارش کمیسیون تولید نشد" in history.text
