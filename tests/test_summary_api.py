"""
تست‌های لایه‌ی API کارگاه خلاصه‌سازی گزارش حسابرسی (``/api/summary/*``).

دقیقاً مطابق الگوی ``test_checklist_api.py``: ``audit_pipeline.start_audit_summary_job``
(تنها نقطه‌ای که ترد پردازش سنگین -- استخراج متن + فراخوانی LLM واقعی -- را
راه‌اندازی می‌کند) با یک نسخه‌ی synchronous جایگزین می‌شود تا تست‌ها سریع و
بدون فراخوانی واقعی LLM/OCR اجرا شوند.
"""
from __future__ import annotations

import io

import audit_pipeline


def _fake_pdf_upload() -> dict:
    return {
        "audit_report": (
            "گزارش.pdf",
            io.BytesIO(b"%PDF-1.4 fake pdf bytes are enough for this test"),
            "application/pdf",
        )
    }


def _make_fake_start_audit_summary_job(monkeypatch, *, status: str = "done", error: str | None = None):
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


def test_create_summary_job_success(client, monkeypatch):
    _make_fake_start_audit_summary_job(monkeypatch)

    response = client.post(
        "/api/summary/jobs",
        data={"organization": "شرکت تست", "meeting_context": "جلسه هیئت‌مدیره"},
        files=_fake_pdf_upload(),
    )

    assert response.status_code == 200
    body = response.json()
    assert "job_id" in body and body["job_id"]


def test_summary_job_status_polling_reaches_done(client, monkeypatch):
    _make_fake_start_audit_summary_job(monkeypatch)

    create_resp = client.post("/api/summary/jobs", data={}, files=_fake_pdf_upload())
    job_id = create_resp.json()["job_id"]

    status_resp = client.get(f"/api/summary/jobs/{job_id}")
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["status"] == "done"
    assert body["progress"] == 100
    assert body["summary_ready"] is True


def test_summary_job_results_and_download(client, monkeypatch):
    _make_fake_start_audit_summary_job(monkeypatch)

    create_resp = client.post("/api/summary/jobs", data={}, files=_fake_pdf_upload())
    job_id = create_resp.json()["job_id"]

    results_resp = client.get(f"/api/summary/jobs/{job_id}/results")
    assert results_resp.status_code == 200
    body = results_resp.json()
    assert "مورد مهم اول" in body["summary_html"]
    assert body["source_filename"] == "گزارش.pdf"

    download_resp = client.get(f"/api/summary/jobs/{job_id}/download")
    assert download_resp.status_code == 200
    assert download_resp.content == b"fake summary docx bytes"
    assert "attachment" in download_resp.headers["content-disposition"]


def test_summary_job_error_status(client, monkeypatch):
    _make_fake_start_audit_summary_job(monkeypatch, status="error", error="خطای شبیه‌سازی‌شده")

    create_resp = client.post("/api/summary/jobs", data={}, files=_fake_pdf_upload())
    job_id = create_resp.json()["job_id"]

    status_resp = client.get(f"/api/summary/jobs/{job_id}")
    body = status_resp.json()
    assert body["status"] == "error"
    assert body["error"] == "خطای شبیه‌سازی‌شده"

    results_resp = client.get(f"/api/summary/jobs/{job_id}/results")
    assert results_resp.status_code == 409


def test_create_summary_job_missing_file_returns_400(client):
    response = client.post("/api/summary/jobs", data={}, files={})
    assert response.status_code == 400
    assert "بارگذاری" in response.json()["detail"]


def test_create_summary_job_invalid_file_extension_returns_400(client):
    files = {"audit_report": ("report.txt", io.BytesIO(b"hello"), "text/plain")}
    response = client.post("/api/summary/jobs", data={}, files=files)
    assert response.status_code == 400
    assert "فرمت" in response.json()["detail"]


def test_summary_job_not_found_returns_404(client):
    response = client.get("/api/summary/jobs/does-not-exist")
    assert response.status_code == 404
