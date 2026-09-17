"""تست هم‌زمانی: چند کارگاه در یک پروژه (و چند پروژه) بدون نشتی حالت به یکدیگر.

هدف اصلی این فایل، اثبات همان بند «تعریف انجام‌شده» است: اجرای هم‌زمان چک‌لیست و
خلاصه‌سازی در یک پروژه باید مستقل بماند -- نه پیشرفت هیچ‌کدام گم شود و نه نتیجه‌ها
با هم قاطی شوند.

برای اینکه بتوانیم «هم‌زمانی» را واقعاً کنترل کنیم، تابع شروع پردازش هر کارگاه با
نسخه‌ای جایگزین می‌شود که job را در وضعیت ``running`` نگه می‌دارد تا تست خودش با
فراخوانی ``release`` آن را تمام کند (شبیه‌سازی یک پردازش طولانی).
"""
from __future__ import annotations

import io

from api.db.models import WorkshopRun
from api.repositories import projects as projects_repo


def _gated_starters(monkeypatch):
    """دو کارگاه را با شروع‌کننده‌های قابل‌کنترل (قفل‌شده) جایگزین می‌کند."""
    import audit_pipeline
    import pipeline

    gates: dict[str, dict] = {"checklist": {}, "audit-summary": {}}

    def make_starter(kind: str):
        def _starter(job, *args, **kwargs):
            job["status"] = "running"
            job["stage"] = f"[{kind}] در حال پردازش (تست)"
            job["logs"] = ["شروع پردازش آزمایشی"]
            gates[kind]["job"] = job

        return _starter

    def release(kind: str, *, status: str = "done", payload: dict | None = None):
        job = gates[kind]["job"]
        if status == "error":
            job["status"] = "error"
            job["error"] = (payload or {}).get("error", "خطای آزمایشی")
            return
        job["status"] = "done"
        job["result"] = payload or {}

    monkeypatch.setattr(pipeline, "start_checklist_job", make_starter("checklist"))
    monkeypatch.setattr(audit_pipeline, "start_audit_summary_job", make_starter("audit-summary"))
    return release


def _xlsx(name: str):
    return (
        name,
        (
            "dummy.xlsx",
            io.BytesIO(b"bytes"),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
    )


def _start_checklist(client, project_id: int) -> dict:
    response = client.post(
        f"/api/projects/{project_id}/checklist/jobs",
        data={"entity_name": "سازمان الف"},
        files=dict([_xlsx("revised_budget"), _xlsx("financial_statements"), _xlsx("balance_sheet")]),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _start_summary(client, project_id: int) -> dict:
    response = client.post(
        f"/api/projects/{project_id}/audit-summary/jobs",
        data={},
        files={"audit_report": ("گزارش.pdf", io.BytesIO(b"%PDF-1.4 x"), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    return response.json()


CHECKLIST_RESULT = {
    "summary": {
        "total": 10,
        "true_count": 9,
        "false_count": 1,
        "error_count": 0,
        "manual_count": 0,
        "compliance_rate": 90.0,
    },
    "warnings": [],
    "checklist_results": [],
    "committee_report": {
        "docx_bytes": b"checklist docx",
        "docx_filename": "گزارش_کمیسیون.docx",
        "used_audit_report_text": False,
    },
    "elapsed_seconds": 3.0,
}

SUMMARY_RESULT = {
    "docx_bytes": b"summary docx",
    "docx_filename": "خلاصه.docx",
    "warnings": [],
    "elapsed_seconds": 4.0,
    "source_filename": "گزارش.pdf",
    "summary_markdown": "# خلاصه\n\n- مورد اول\n",
}


def test_two_workshops_run_concurrently_in_one_project(auth_client, db_session, project, monkeypatch, wait_for_run):
    release = _gated_starters(monkeypatch)

    checklist_job = _start_checklist(auth_client, project.id)
    summary_job = _start_summary(auth_client, project.id)

    assert checklist_job["job_id"] != summary_job["job_id"]
    assert checklist_job["run_id"] != summary_job["run_id"]

    # هر دو هم‌زمان در پنل دیده می‌شوند و هرکدام وضعیت مستقل خودش را دارد
    panel = auth_client.get(f"/api/projects/{project.id}/jobs").json()["jobs"]
    assert {item["status"] for item in panel} == {"running"}
    assert {item["workshop"] for item in panel} == {"checklist", "audit-summary"}

    checklist_status = auth_client.get(
        f"/api/projects/{project.id}/checklist/jobs/{checklist_job['job_id']}"
    ).json()
    summary_status = auth_client.get(
        f"/api/projects/{project.id}/audit-summary/jobs/{summary_job['job_id']}"
    ).json()
    assert checklist_status["stage"].startswith("[checklist]")
    assert summary_status["stage"].startswith("[audit-summary]")

    # پایان کار چک‌لیست نباید روی کار خلاصه‌سازی اثری بگذارد
    release("checklist", payload=CHECKLIST_RESULT)
    checklist_status = auth_client.get(
        f"/api/projects/{project.id}/checklist/jobs/{checklist_job['job_id']}"
    ).json()
    assert checklist_status["status"] == "done"
    assert checklist_status["summary"]["false_count"] == 1

    summary_status = auth_client.get(
        f"/api/projects/{project.id}/audit-summary/jobs/{summary_job['job_id']}"
    ).json()
    assert summary_status["status"] == "running"

    release("audit-summary", payload=SUMMARY_RESULT)
    summary_status = auth_client.get(
        f"/api/projects/{project.id}/audit-summary/jobs/{summary_job['job_id']}"
    ).json()
    assert summary_status["status"] == "done"

    # تاریخچه: دو ردیف مستقل با نتایج خودشان
    wait_for_run(project.id, checklist_job["run_id"])
    wait_for_run(project.id, summary_job["run_id"])

    checklist_run = db_session.get(WorkshopRun, checklist_job["run_id"])
    summary_run = db_session.get(WorkshopRun, summary_job["run_id"])
    db_session.refresh(checklist_run)
    db_session.refresh(summary_run)

    assert checklist_run.workshop_type == "checklist"
    assert summary_run.workshop_type == "audit-summary"
    assert checklist_run.result_summary["false_count"] == 1
    assert summary_run.result_summary["source_filename"] == "گزارش.pdf"
    assert checklist_run.result_file_path != summary_run.result_file_path

    checklist_download = auth_client.get(
        f"/api/projects/{project.id}/checklist/runs/{checklist_run.id}/download"
    )
    summary_download = auth_client.get(
        f"/api/projects/{project.id}/audit-summary/runs/{summary_run.id}/download"
    )
    assert checklist_download.content == b"checklist docx"
    assert summary_download.content == b"summary docx"


def test_same_workshop_can_run_twice_in_parallel(auth_client, db_session, project, monkeypatch, wait_for_run):
    """دو اجرای هم‌زمان از *یک* کارگاه هم مستقل‌اند (کلید job = شناسهٔ یکتا)."""
    release = _gated_starters(monkeypatch)
    import pipeline

    started: list[dict] = []
    original = pipeline.start_checklist_job

    def _starter(job, *args, **kwargs):
        original(job, *args, **kwargs)
        job["status"] = "running"
        started.append(job)

    monkeypatch.setattr(pipeline, "start_checklist_job", _starter)

    first = _start_checklist(auth_client, project.id)
    second = _start_checklist(auth_client, project.id)
    assert first["job_id"] != second["job_id"]
    assert len(started) == 2

    # پایان دادن به اجرای دوم، اجرای اول را دست‌نخورده می‌گذارد
    started[1]["status"] = "done"
    started[1]["result"] = dict(CHECKLIST_RESULT, committee_report=dict(CHECKLIST_RESULT["committee_report"], docx_bytes=b"second"))

    first_status = auth_client.get(f"/api/projects/{project.id}/checklist/jobs/{first['job_id']}").json()
    second_status = auth_client.get(f"/api/projects/{project.id}/checklist/jobs/{second['job_id']}").json()
    assert second_status["status"] == "done"
    assert first_status["status"] == "running"

    started[0]["status"] = "done"
    started[0]["result"] = dict(CHECKLIST_RESULT, committee_report=dict(CHECKLIST_RESULT["committee_report"], docx_bytes=b"first"))

    wait_for_run(project.id, first["run_id"])
    wait_for_run(project.id, second["run_id"])

    first_download = auth_client.get(f"/api/projects/{project.id}/checklist/runs/{first['run_id']}/download")
    second_download = auth_client.get(f"/api/projects/{project.id}/checklist/runs/{second['run_id']}/download")
    assert first_download.content == b"first"
    assert second_download.content == b"second"


def test_jobs_in_different_projects_are_isolated(auth_client, db_session, project, monkeypatch, wait_for_run):
    release = _gated_starters(monkeypatch)
    other_project = projects_repo.create(db_session, project.user_id, "پروژهٔ دوم")

    first = _start_checklist(auth_client, project.id)
    second = _start_summary(auth_client, other_project.id)

    # پنل هر پروژه فقط اجراهای خودش را نشان می‌دهد
    panel_one = auth_client.get(f"/api/projects/{project.id}/jobs").json()["jobs"]
    panel_two = auth_client.get(f"/api/projects/{other_project.id}/jobs").json()["jobs"]
    assert [item["workshop"] for item in panel_one] == ["checklist"]
    assert [item["workshop"] for item in panel_two] == ["audit-summary"]

    # job هر پروژه از مسیر پروژهٔ دیگر قابل دسترسی نیست
    assert auth_client.get(
        f"/api/projects/{other_project.id}/checklist/jobs/{first['job_id']}"
    ).status_code == 404
    assert auth_client.get(
        f"/api/projects/{project.id}/audit-summary/jobs/{second['job_id']}"
    ).status_code == 404

    release("checklist", payload=CHECKLIST_RESULT)
    release("audit-summary", payload=SUMMARY_RESULT)

    assert wait_for_run(project.id, first["run_id"])["status"] == "done"
    assert wait_for_run(other_project.id, second["run_id"])["status"] == "done"


# ---------------------------------------------------------------------------
# کارگاه سوم (تحلیل بودجه) هم در همان پروژه و هم‌زمان مستقل می‌ماند
# ---------------------------------------------------------------------------
def _gated_budget_starter(monkeypatch):
    """``start_budget_job`` را با نسخه‌ای قابل‌کنترل (قفل‌شده) جایگزین می‌کند.

    مثل دو کارگاه دیگر: ترد واقعی راه نمی‌افتد و job در وضعیت ``running`` می‌ماند
    تا خودِ تست با ``release`` آن را تمام کند -- بنابراین می‌توان «هم‌زمانی» را
    قطعی آزمود.
    """
    import budget_analysis.pipeline as budget_pipeline

    gate: dict = {}

    def _starter(job, file_paths, workdir, **kwargs):
        job["status"] = "running"
        job["stage"] = "[budget-analysis] در حال پردازش (تست)"
        job["logs"] = ["شروع پردازش آزمایشی"]
        gate["job"] = job

    monkeypatch.setattr(budget_pipeline, "start_budget_job", _starter)
    return gate


def _start_budget(client, project_id: int, organization: str = "سازمان ب") -> dict:
    response = client.post(
        f"/api/projects/{project_id}/budget-analysis/jobs",
        data={"organization": organization},
        files=dict([_xlsx("budget_base_year"), _xlsx("budget_current_year")]),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_three_workshops_run_concurrently_in_one_project(
    auth_client, db_session, project, monkeypatch, wait_for_run
):
    """هر سه کارگاه هم‌زمان در یک پروژه: نه پیشرفت و نه نتیجه‌ی هیچ‌کدام قاطی نمی‌شود."""
    release = _gated_starters(monkeypatch)
    budget_gate = _gated_budget_starter(monkeypatch)

    checklist_job = _start_checklist(auth_client, project.id)
    summary_job = _start_summary(auth_client, project.id)
    budget_job = _start_budget(auth_client, project.id)

    job_ids = {checklist_job["job_id"], summary_job["job_id"], budget_job["job_id"]}
    run_ids = {checklist_job["run_id"], summary_job["run_id"], budget_job["run_id"]}
    assert len(job_ids) == 3, "شناسه‌ی job ها باید یکتا باشند"
    assert len(run_ids) == 3, "هر اجرا ردیف تاریخچه‌ی خودش را دارد"

    # پنل پروژه هر سه را هم‌زمان و مستقل نشان می‌دهد
    panel = auth_client.get(f"/api/projects/{project.id}/jobs").json()["jobs"]
    assert {item["status"] for item in panel} == {"running"}
    assert {item["workshop"] for item in panel} == {"checklist", "audit-summary", "budget-analysis"}
    assert {item["run_id"] for item in panel} == run_ids

    # وضعیت زنده‌ی هر سه از مسیر کارگاه خودش
    stages = {
        "checklist": auth_client.get(
            f"/api/projects/{project.id}/checklist/jobs/{checklist_job['job_id']}"
        ).json()["stage"],
        "audit-summary": auth_client.get(
            f"/api/projects/{project.id}/audit-summary/jobs/{summary_job['job_id']}"
        ).json()["stage"],
        "budget-analysis": auth_client.get(
            f"/api/projects/{project.id}/budget-analysis/jobs/{budget_job['job_id']}"
        ).json()["stage"],
    }
    assert stages["checklist"].startswith("[checklist]")
    assert stages["audit-summary"].startswith("[audit-summary]")
    assert stages["budget-analysis"].startswith("[budget-analysis]")

    # پایان کار چک‌لیست هیچ اثری روی دو کار دیگر ندارد
    release("checklist", payload=CHECKLIST_RESULT)
    assert auth_client.get(
        f"/api/projects/{project.id}/checklist/jobs/{checklist_job['job_id']}"
    ).json()["status"] == "done"
    for other_job_id, slug in ((summary_job["job_id"], "audit-summary"), (budget_job["job_id"], "budget-analysis")):
        assert auth_client.get(
            f"/api/projects/{project.id}/{slug}/jobs/{other_job_id}"
        ).json()["status"] == "running"

    # تحلیل بودجه با نتیجه‌ی خودش تمام می‌شود (بدون گزارش Word در این تست)
    budget_gate["job"]["status"] = "done"
    budget_gate["job"]["result"] = {"warnings": ["هشدار مخصوص تحلیل بودجه"]}
    release("audit-summary", payload=SUMMARY_RESULT)

    # تاریخچه: سه ردیف مستقل، هرکدام با داده‌های خودش
    checklist_run = wait_for_run(project.id, checklist_job["run_id"])
    summary_run = wait_for_run(project.id, summary_job["run_id"])
    budget_run = wait_for_run(project.id, budget_job["run_id"])

    assert checklist_run["workshop"] == "checklist"
    assert summary_run["workshop"] == "audit-summary"
    assert budget_run["workshop"] == "budget-analysis"

    assert checklist_run["status"] == "done" and checklist_run["download_ready"] is True
    assert checklist_run["result_summary"]["false_count"] == 1
    assert checklist_run["result_summary"]["result_filename"].endswith(".docx")

    assert summary_run["status"] == "done" and summary_run["download_ready"] is True
    assert summary_run["result_summary"]["source_filename"] == "گزارش.pdf"

    # نتیجه‌ی تحلیل بودجه فقط داده‌های خودش را دارد: نه شمارش چک‌لیست، نه فایل خلاصه
    assert budget_run["status"] == "done"
    assert budget_run["result_file_path"] is None
    assert budget_run["result_summary"]["organization"] == "سازمان ب"
    assert "false_count" not in budget_run["result_summary"]
    assert "source_filename" not in budget_run["result_summary"]

    # فایل‌های نتیجه هم از مسیر همان کارگاه دانلود می‌شوند و بایت‌هایشان قاطی نمی‌شود
    assert auth_client.get(
        f"/api/projects/{project.id}/checklist/runs/{checklist_job['run_id']}/download"
    ).content == b"checklist docx"
    assert auth_client.get(
        f"/api/projects/{project.id}/audit-summary/runs/{summary_job['run_id']}/download"
    ).content == b"summary docx"
    # تحلیل بودجه فایلی ندارد و دانلود از مسیر کارگاه دیگر هم نباید آن را بدهد
    assert auth_client.get(
        f"/api/projects/{project.id}/budget-analysis/runs/{budget_job['run_id']}/download"
    ).status_code == 404


def test_job_of_one_workshop_is_not_readable_through_another(auth_client, project, monkeypatch):
    """شناسه‌ی job هر کارگاه فقط از مسیر کارگاه خودش خوانده می‌شود.

    پیش‌تر بازیابی job فقط مالکیت *پروژه* را بررسی می‌کرد؛ در نتیجه یک job
    چک‌لیست از مسیر تحلیل بودجه قابل خواندن بود و پاسخ هم با منطق اشتباه
    (پیشرفت و آماده‌بودن گزارشِ همان کارگاه دیگر) ساخته می‌شد.
    """
    release = _gated_starters(monkeypatch)
    budget_gate = _gated_budget_starter(monkeypatch)

    checklist_job = _start_checklist(auth_client, project.id)
    summary_job = _start_summary(auth_client, project.id)
    budget_job = _start_budget(auth_client, project.id)

    cases = [
        ("checklist", checklist_job["job_id"]),
        ("audit-summary", summary_job["job_id"]),
        ("budget-analysis", budget_job["job_id"]),
    ]
    endpoints = ["checklist", "audit-summary", "budget-analysis"]
    for owner_slug, job_id in cases:
        for endpoint_slug in endpoints:
            # مسیر نتایج هم دقیقاً همان قاعده را دارد
            for suffix in ("", "/results"):
                response = auth_client.get(
                    f"/api/projects/{project.id}/{endpoint_slug}/jobs/{job_id}{suffix}"
                )
                if endpoint_slug == owner_slug:
                    assert response.status_code in (200, 409), (
                        f"{owner_slug} باید از مسیر خودش خوانده شود ({suffix})"
                    )
                else:
                    assert response.status_code == 404, (
                        f"job {owner_slug} نباید از مسیر {endpoint_slug} خوانده شود ({suffix})"
                    )

    # و دانلود نتیجه هم فقط از مسیر کارگاه صاحبِ ردیف کار می‌کند
    # (جزئیات با فایل نتیجه‌ی واقعی در تست بعدی سنجیده می‌شود)
    release("checklist", payload=CHECKLIST_RESULT)
    budget_gate["job"]["status"] = "error"
    budget_gate["job"]["error"] = "خطای آزمایشی"
    release("audit-summary", payload=SUMMARY_RESULT)


def test_run_download_is_scoped_to_its_own_workshop(
    auth_client, project, monkeypatch, wait_for_run
):
    """ردیف تاریخچه‌ی یک کارگاه از مسیر دانلود کارگاه دیگر قابل دریافت نیست."""
    release = _gated_starters(monkeypatch)

    checklist_job = _start_checklist(auth_client, project.id)
    release("checklist", payload=CHECKLIST_RESULT)
    wait_for_run(project.id, checklist_job["run_id"])

    run_id = checklist_job["run_id"]
    own = auth_client.get(f"/api/projects/{project.id}/checklist/runs/{run_id}/download")
    assert own.status_code == 200
    assert own.content == b"checklist docx"

    for other_slug in ("audit-summary", "budget-analysis"):
        foreign = auth_client.get(
            f"/api/projects/{project.id}/{other_slug}/runs/{run_id}/download"
        )
        assert foreign.status_code == 404, f"ردیف چک‌لیست از مسیر {other_slug} لو رفت"


# ---------------------------------------------------------------------------
# رجیستری job ها: آزمون منطق خالص (بدون مرورگر و بدون HTTP)
# ---------------------------------------------------------------------------
def test_job_manager_tracks_jobs_of_different_workshops_independently():
    """هر job دیکشنری مستقل خودش را دارد و برچسب‌های پروژه/اجرا/کارگاهش را نگه می‌دارد."""
    import budget_analysis.pipeline as budget_pipeline
    import pipeline

    from api.jobs.job_manager import JobManager

    manager = JobManager(ttl_seconds=60)
    checklist_a_id, checklist_a = manager.create(
        pipeline.new_checklist_job, kind="checklist", project_id=1, run_id=11
    )
    budget_id, budget_job = manager.create(
        budget_pipeline.new_budget_job, kind="budget-analysis", project_id=1, run_id=12
    )
    checklist_b_id, checklist_b = manager.create(
        pipeline.new_checklist_job, kind="checklist", project_id=2, run_id=13
    )

    assert len({checklist_a_id, budget_id, checklist_b_id}) == 3

    # نوشتن در یک job هیچ اثری روی بقیه ندارد
    checklist_a["status"] = "running"
    checklist_a["stage"] = "الف"
    budget_job["status"] = "running"
    budget_job["stage"] = "ب"
    assert manager.get(checklist_a_id)["stage"] == "الف"
    assert manager.get(budget_id)["stage"] == "ب"
    assert checklist_b["status"] == "pending"

    # پرس‌وجوها بر اساس برچسب‌ها
    assert {job["_job_id"] for job in manager.list_for_project(1)} == {checklist_a_id, budget_id}
    assert [job["_job_id"] for job in manager.list_for_project(2)] == [checklist_b_id]
    assert manager.get_by_run(11)["_kind"] == "checklist"
    assert manager.get_by_run(12)["_kind"] == "budget-analysis"
    assert manager.get_by_run(999) is None

    # حذف یک پروژه فقط job های همان پروژه را برمی‌دارد (پروژه‌ی دیگر دست‌نخورده)
    assert manager.delete_for_project(1) == 2
    assert manager.get(checklist_a_id) is None
    assert manager.get(budget_id) is None
    assert manager.get(checklist_b_id) is not None


def test_job_manager_purges_expired_jobs_but_never_running_ones():
    """پاک‌سازی خودکار فقط job های تمام‌شده‌ی منقضی را برمی‌دارد."""
    import pipeline

    from api.jobs.job_manager import JobManager

    manager = JobManager(ttl_seconds=10)
    finished_id, finished = manager.create(pipeline.new_checklist_job, kind="checklist")
    running_id, running = manager.create(pipeline.new_checklist_job, kind="checklist")
    failed_id, failed = manager.create(pipeline.new_checklist_job, kind="checklist")

    finished["status"] = "done"
    running["status"] = "running"
    failed["status"] = "error"

    # هر سه از نظر زمانی «قدیمی» می‌شوند
    for job in (finished, running, failed):
        job["_created_at"] -= 100

    manager.purge_expired()

    assert manager.get(finished_id) is None
    assert manager.get(failed_id) is None
    # اجرای در جریان هرگز پاک نمی‌شود، حتی اگر مدتی طول بکشد
    assert manager.get(running_id) is not None


def test_job_manager_keeps_two_runs_of_the_same_workshop_apart():
    """دو اجرای هم‌زمان از *یک* کارگاه در یک پروژه دو job و دو ردیف جدا دارند."""
    import pipeline

    from api.jobs.job_manager import JobManager

    manager = JobManager(ttl_seconds=60)
    first_id, first = manager.create(pipeline.new_checklist_job, kind="checklist", project_id=7, run_id=1)
    second_id, second = manager.create(pipeline.new_checklist_job, kind="checklist", project_id=7, run_id=2)

    assert first_id != second_id
    first["status"] = "error"
    first["error"] = "خطای اجرای اول"

    assert manager.get(first_id)["error"] == "خطای اجرای اول"
    assert manager.get(second_id)["status"] == "pending"
    assert "error" not in manager.get(second_id) or manager.get(second_id)["error"] is None
    assert manager.get_by_run(1) is manager.get(first_id)
    assert manager.get_by_run(2) is manager.get(second_id)

