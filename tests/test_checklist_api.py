"""
تست‌های لایه‌ی API کارگاه چک‌لیست حسابرسی (``/api/checklist/*``).

طبق محدوده‌ی فاز ۳: هیچ منطق استخراج/چک‌لیست واقعی این‌جا اجرا یا تست نمی‌شود؛
``pipeline.start_checklist_job`` (تنها نقطه‌ای که thread پردازش سنگین واقعی را
راه‌اندازی می‌کند) با یک نسخه‌ی synchronous جایگزین (monkeypatch) می‌شود تا
تست‌ها سریع، بدون وابستگی خارجی (بدون نیاز به pandas parsing واقعی، OCR یا
فراخوانی LLM) اجرا شوند.
"""
from __future__ import annotations

import io

import pipeline


def _fake_xlsx_upload(name: str) -> tuple[str, tuple[str, io.BytesIO, str]]:
    """یک فایل xlsx مصنوعی -- ``pipeline.save_uploaded_file`` برای پسوند xlsx
    صرفاً بایت‌های خام را روی دیسک می‌نویسد (بدون parse کردن)، پس محتوای
    واقعی اکسل لازم نیست."""
    return name, ("dummy.xlsx", io.BytesIO(b"not a real xlsx, but bytes are enough"), (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ))


def _required_files() -> dict:
    return dict(
        [
            _fake_xlsx_upload("revised_budget"),
            _fake_xlsx_upload("financial_statements"),
            _fake_xlsx_upload("balance_sheet"),
        ]
    )


def _make_fake_start_checklist_job(monkeypatch, *, status: str = "done", error: str | None = None):
    """جایگزین ``pipeline.start_checklist_job`` که به‌جای اجرای واقعی
    ``run_full_pipeline`` در یک ترد پس‌زمینه، بلافاصله و synchronous دیکشنری
    job را با یک نتیجه‌ی نمونه (یا خطا) پر می‌کند."""

    def _fake(job, file_paths, workdir, audit_report_path=None, entity_name=None):
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


def test_create_checklist_job_success(client, monkeypatch):
    _make_fake_start_checklist_job(monkeypatch)

    response = client.post(
        "/api/checklist/jobs",
        data={"entity_name": "شرکت تست"},
        files=_required_files(),
    )

    assert response.status_code == 200
    body = response.json()
    assert "job_id" in body and body["job_id"]


def test_checklist_job_status_polling_reaches_done(client, monkeypatch):
    _make_fake_start_checklist_job(monkeypatch)

    create_resp = client.post("/api/checklist/jobs", data={}, files=_required_files())
    job_id = create_resp.json()["job_id"]

    status_resp = client.get(f"/api/checklist/jobs/{job_id}")
    assert status_resp.status_code == 200
    status_body = status_resp.json()
    assert status_body["status"] == "done"
    assert status_body["progress"] == 100
    assert status_body["report_ready"] is True
    assert status_body["summary"]["total"] == 4


def test_checklist_job_results_and_report_download(client, monkeypatch):
    _make_fake_start_checklist_job(monkeypatch)

    create_resp = client.post("/api/checklist/jobs", data={}, files=_required_files())
    job_id = create_resp.json()["job_id"]

    results_resp = client.get(f"/api/checklist/jobs/{job_id}/results")
    assert results_resp.status_code == 200
    results_body = results_resp.json()
    assert results_body["summary"]["false_count"] == 1
    assert len(results_body["items"]) == 1
    assert results_body["items"][0]["question_id"] == "Q1"

    report_resp = client.get(f"/api/checklist/jobs/{job_id}/report")
    assert report_resp.status_code == 200
    assert report_resp.content == b"fake docx bytes"
    assert "attachment" in report_resp.headers["content-disposition"]


def test_checklist_job_error_status(client, monkeypatch):
    _make_fake_start_checklist_job(monkeypatch, status="error", error="خطای شبیه‌سازی‌شده")

    create_resp = client.post("/api/checklist/jobs", data={}, files=_required_files())
    job_id = create_resp.json()["job_id"]

    status_resp = client.get(f"/api/checklist/jobs/{job_id}")
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["status"] == "error"
    assert body["error"] == "خطای شبیه‌سازی‌شده"

    # نتایج هنوز آماده نیست -> باید 409 برگرداند، نه یک 500/کرش.
    results_resp = client.get(f"/api/checklist/jobs/{job_id}/results")
    assert results_resp.status_code == 409


def test_create_checklist_job_missing_required_file_returns_400(client):
    response = client.post("/api/checklist/jobs", data={}, files={})
    assert response.status_code == 400
    assert "الزامی" in response.json()["detail"]


def test_create_checklist_job_invalid_file_extension_returns_400(client):
    files = _required_files()
    # فایل «فایل بودجه اصلاحیه» را با یک پسوند غیرمجاز جایگزین می‌کنیم.
    files["revised_budget"] = ("budget.txt", io.BytesIO(b"hello"), "text/plain")

    response = client.post("/api/checklist/jobs", data={}, files=files)
    assert response.status_code == 400
    assert "فرمت" in response.json()["detail"]


def test_checklist_job_not_found_returns_404(client):
    response = client.get("/api/checklist/jobs/does-not-exist")
    assert response.status_code == 404
