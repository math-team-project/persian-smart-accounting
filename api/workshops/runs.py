"""اجرای کارگاه‌ها: ثبت ردیف تاریخچه، پنل کارهای در جریان، و ثبت نتیجه.

این ماژول پل بین سه چیز است:
  ۱) ``job_manager`` -- وضعیت زنده‌ی in-memory هر اجرا،
  ۲) پایگاه‌داده داشبورد -- ردیف ``workshop_runs`` (تاریخچه، جدیدترین اول)،
  ۳) ``api/storage.py`` -- فایل نتیجه‌ی هر اجرا روی دیسک.

نکته‌ی کلیدی جداسازی: هیچ‌کدام از توابع این ماژول به ``pipeline.py`` یا
``audit_pipeline.py`` دست نمی‌زنند. ثبت تاریخچه از طریق callback ای انجام می‌شود
که ``JobManager.watch_lifecycle`` پس از رسیدن job به وضعیت نهایی صدا می‌زند
(``make_finalizer``) -- یعنی ``pipeline.py``/``audit_pipeline.py`` کاملاً
دست‌نخورده می‌مانند.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from api.db.base import SessionLocal
from api.db.models import WorkshopRun, utcnow
from api.jobs.job_manager import job_manager as default_job_manager
from api.repositories import workshop_runs as runs_repo

# نام مستعار: در این ماژول واژه‌ی ``storage`` می‌تواند با مفهوم «ذخیره‌سازی ردیف»
# اشتباه گرفته شود؛ ``result_storage`` صریحاً یعنی فایل نتیجه روی دیسک.
from api import storage as result_storage
from api.workshops.registry import WorkshopDefinition, get as get_workshop

logger = logging.getLogger(__name__)

# تعداد اجراهای تمام‌شده‌ای که در پنل «کارهای در جریان» هم نمایش داده می‌شوند
# (تا کاربر بلافاصله پس از پایان کار، دکمه‌ی دانلود نتیجه را ببیند).
RECENT_FINISHED_IN_PANEL = 5

_ACTIVE_STATUSES = ("pending", "running")


def start_run(session: Session, project_id: int, workshop_slug: str) -> WorkshopRun:
    """ردیف تاریخچه را پیش از شروع پردازش می‌سازد تا اجرا بلافاصله در پنل دیده شود."""
    return runs_repo.create(session, project_id, workshop_slug)


def make_finalizer(run_id: int) -> Callable[[dict[str, Any]], None]:
    """callback ای که ``JobManager`` پس از پایان job صدا می‌زند (در ترد نگهبان)."""

    def _finalize(job: dict[str, Any]) -> None:
        finalize_run(run_id, job)

    return _finalize


def finalize_run(run_id: int, job: dict[str, Any]) -> None:
    """ثبت نتیجه‌ی نهایی یک اجرا: فایل خروجی روی دیسک + متادیتا در تاریخچه.

    این تابع در ترد پس‌زمینه فراخوانی می‌شود، بنابراین نشست پایگاه‌داده‌ی جداگانه‌ی
    خودش را باز می‌کند (نشست درخواست HTTP در آن لحظه بسته شده است).
    """
    status = "done" if job.get("status") == "done" else "error"
    slug = job.get("_kind") or ""
    workshop = get_workshop(slug)

    with SessionLocal() as session:
        run = session.get(WorkshopRun, run_id)
        if run is None:
            # پروژه (و در نتیجه ردیف اجرا) در همین فاصله حذف شده است.
            logger.info("run %s no longer exists; skipping history update", run_id)
            return
        project_id = run.project_id

        result_summary: dict[str, Any] = {}
        result_file_path: Optional[str] = None

        if status == "error" or workshop is None:
            result_summary = {"error": job.get("error") or "پردازش با خطا متوقف شد."}
        else:
            try:
                artifact = workshop.collect_result(job)
            except Exception as exc:  # noqa: BLE001 -- نتیجه‌ی نامعتبر نباید اجرا را خراب کند
                logger.exception("collecting result of run %s failed", run_id)
                # خودِ اجرا موفق بوده؛ فقط آماده‌سازی نتیجه شکست خورده است -- بنابراین
                # به‌عنوان «گزارش‌خطا» ثبت می‌شود (نه «خطای اجرا») و در تاریخچه هم
                # به‌صورت هشدار دیده می‌شود، نه به‌عنوان پردازش ناموفق.
                result_summary = {"report_error": f"خطا در آماده‌سازی نتیجه: {exc}"}
            else:
                result_summary = dict(artifact.summary)
                if artifact.content and artifact.filename:
                    try:
                        result_file_path, _ = result_storage.save_result_file(
                            project_id, run_id, artifact.filename, artifact.content
                        )
                    except OSError as exc:
                        logger.exception("saving result file of run %s failed", run_id)
                        result_summary["file_error"] = f"ذخیره‌ی فایل نتیجه ناموفق بود: {exc}"

        runs_repo.finish(
            session,
            run_id,
            status=status,
            result_summary=result_summary,
            result_file_path=result_file_path,
        )
        logger.info(
            "run %s finalized (project=%s, workshop=%s, status=%s, file=%s)",
            run_id,
            project_id,
            slug,
            status,
            result_file_path,
        )


# ---------------------------------------------------------------------------
# پنل «کارهای در جریان» -- وضعیت زنده از job_manager، بقیه از تاریخچه
# ---------------------------------------------------------------------------
def _live_status(run: WorkshopRun, job: Optional[dict[str, Any]]) -> dict[str, Any]:
    workshop = get_workshop(run.workshop_type)
    if job is not None:
        progress = workshop.estimate_progress(job) if workshop else 0
        return {
            "status": job.get("status", run.status),
            "stage": job.get("stage", ""),
            "progress": progress,
            "error": job.get("error"),
            "live": True,
            "download_ready": bool(workshop and workshop.has_download(job)),
        }
    # job از حافظه رفته (ری‌استارت سرور یا انقضای TTL) -- فقط واقعیتِ تاریخچه می‌ماند.
    return {
        "status": run.status,
        "stage": "پردازش در حافظه‌ی سرور موجود نیست." if run.status in _ACTIVE_STATUSES else "",
        "progress": 100 if run.status == "done" else 0,
        "error": (run.result_summary or {}).get("error") if run.status == "error" else None,
        "live": False,
        "download_ready": bool(run.result_file_path),
    }


def serialize_run(run: WorkshopRun, job: Optional[dict[str, Any]]) -> dict[str, Any]:
    """شکل JSON یک ردیف اجرا برای پنل و برای پاسخ‌های API."""
    workshop = get_workshop(run.workshop_type)
    live = _live_status(run, job)
    return {
        "run_id": run.id,
        "job_id": (job or {}).get("_job_id"),
        "workshop": run.workshop_type,
        "workshop_name_fa": workshop.display_name_fa if workshop else run.workshop_type,
        "workshop_icon": workshop.icon if workshop else "circle",
        "status": live["status"],
        "stage": live["stage"],
        "progress": live["progress"],
        "error": live["error"],
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "result_summary": run.result_summary,
        "download_url": workshop.run_download_url(run.project_id, run.id) if workshop else None,
        "download_ready": live["download_ready"],
        "workshop_url": workshop.page_url(run.project_id) if workshop else None,
    }


def panel_runs(
    session: Session,
    project_id: int,
    *,
    manager=default_job_manager,
    recent_limit: int = RECENT_FINISHED_IN_PANEL,
) -> list[dict[str, Any]]:
    """اجراهای در جریان + چند اجرای اخیر تمام‌شده، با وضعیت زنده‌ی هرکدام."""
    runs = runs_repo.list_for_project(session, project_id, limit=100)
    active = [run for run in runs if run.status in _ACTIVE_STATUSES]
    finished = [run for run in runs if run.status not in _ACTIVE_STATUSES][:recent_limit]

    # اجراهای در جریان در بالای فهرست (جدیدترین اول)، سپس تازه‌ترین تمام‌شده‌ها.
    return [serialize_run(run, manager.get_by_run(run.id)) for run in [*active, *finished]]


# ---------------------------------------------------------------------------
# دانلود نتیجه‌ی یک اجرا (هم برای اجرای در جریان، هم برای تاریخچه)
# ---------------------------------------------------------------------------
class DownloadUnavailable(Exception):
    """برای این اجرا فایل نتیجه‌ای وجود ندارد (خطا، یا هنوز آماده نشده)."""


def read_run_download(
    session: Session,
    project_id: int,
    run_id: int,
    *,
    workshop_slug: Optional[str] = None,
    manager=default_job_manager,
) -> tuple[bytes, str]:
    """محتوای فایل نتیجه‌ی یک اجرا را برمی‌گرداند: ``(bytes, filename)``.

    اول از دیسک می‌خواند (حالت عادی: اجرای تمام‌شده در تاریخچه). اگر فایل هنوز
    نوشته نشده باشد -- مثلاً کاربر بلافاصله پس از دیدن وضعیت ``done`` دانلود را
    می‌زند و ترد نگهبان هنوز ثبت نکرده است -- به بایت‌های زنده‌ی همان job در حافظه
    برمی‌گردد. این ترتیب دقیقاً برای از بین بردن همان حالت مسابقه‌ی کوچک است.

    ``workshop_slug`` اگر داده شود، ردیف باید متعلق به همان کارگاه باشد؛ وگرنه
    نتیجه‌ی یک کارگاه از مسیر دانلود کارگاه دیگر قابل دریافت می‌شد (همان نشتی
    حالت بین کارگاه‌ها که در ``api/utils/jobs.py`` هم بسته شده است).
    """
    run = runs_repo.get(session, project_id, run_id)
    if run is None or (workshop_slug is not None and run.workshop_type != workshop_slug):
        raise runs_repo.RunNotFound(f"run {run_id} not found in project {project_id}")

    if run.result_file_path:
        content = result_storage.read_result_file(run.result_file_path)
        if content:
            filename = (run.result_summary or {}).get("result_filename") or "نتیجه.docx"
            return content, filename

    workshop = get_workshop(run.workshop_type)
    job = manager.get_by_run(run_id)
    if workshop is not None and job is not None and job.get("status") == "done":
        artifact = workshop.collect_result(job)
        if artifact.content and artifact.filename:
            return artifact.content, artifact.filename

    raise DownloadUnavailable("فایل نتیجه‌ی این اجرا در دسترس نیست.")


# ---------------------------------------------------------------------------
# تاریخچه‌ی پروژه (برای رندر سرور-ساید؛ بدون نیاز به جاوااسکریپت)
# ---------------------------------------------------------------------------
def runs_for_history(session: Session, project_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    """ردیف‌های تاریخچه، جدیدترین اول، آماده برای نمایش در قالب."""
    rows: list[dict[str, Any]] = []
    for run in runs_repo.list_for_project(session, project_id, limit=limit):
        workshop = get_workshop(run.workshop_type)
        summary = run.result_summary or {}
        rows.append(
            {
                "id": run.id,
                "workshop_name_fa": workshop.display_name_fa if workshop else run.workshop_type,
                "icon": workshop.icon if workshop else "circle",
                "accent": workshop.accent if workshop else "slate",
                "status": run.status,
                "created_at": run.created_at,
                "finished_at": run.finished_at,
                "error": _short(summary.get("error")) if run.status == "error" else None,
                # اجرای موفق اما بدون گزارش (مثلاً خطای سرویس هوش مصنوعی در تولید
                # گزارش کمیسیون) -- جدا از «خطای اجرا» نمایش داده می‌شود.
                "report_error": _short(summary.get("report_error")),
                # اجرا موفق بوده اما ذخیره‌ی فایل نتیجه روی دیسک شکست خورده است.
                "file_error": _short(summary.get("file_error")),
                "chips_fa": _history_chips(workshop, summary),
                "download_url": (
                    workshop.run_download_url(project_id, run.id)
                    if workshop and run.result_file_path
                    else None
                ),
                "workshop_url": workshop.page_url(project_id) if workshop else None,
            }
        )
    return rows


MAX_DISPLAY_ERROR_CHARS = 240


def _short(text: Optional[str]) -> Optional[str]:
    """پیام خطای ذخیره‌شده را برای *نمایش* کوتاه می‌کند.

    خطاهای سرویس‌های بیرونی (مثلاً هوش مصنوعی) گاهی یک JSON بلند با شناسهٔ درخواست
    هستند؛ متن کامل در ``result_summary`` باقی می‌ماند و این‌جا فقط ظاهر فهرست
    تاریخچه مرتب نگه داشته می‌شود.
    """
    if not text:
        return None
    text = str(text)
    if len(text) <= MAX_DISPLAY_ERROR_CHARS:
        return text
    return text[:MAX_DISPLAY_ERROR_CHARS].rstrip() + "…"


def _history_chips(workshop: Optional[WorkshopDefinition], summary: dict[str, Any]) -> list[str]:
    """چند برچسب کوتاه خلاصه‌ی نتیجه -- قالب تاریخچه کاملاً عمومی می‌ماند و
    کارگاه خودش می‌داند کدام اعدادش برای نمایش مهم‌اند."""
    if workshop is None or not summary:
        return []
    try:
        return list(workshop.summary_chips_fa(summary))
    except Exception:  # noqa: BLE001 -- نمایش تاریخچه هرگز نباید به‌خاطر یک ردیف بشکند
        logger.exception("building history chips failed for workshop %s", workshop.slug)
        return []


def mark_orphaned_runs(session: Session) -> int:
    """اجراهای «در جریان» باقی‌مانده از اجرای قبلی سرور را ناموفق علامت می‌زند.

    وضعیت job ها در حافظه‌ی همین فرآیند است؛ پس هر ردیفی که پس از راه‌اندازی سرور
    هنوز pending/running باشد متعلق به فرآیندی است که دیگر وجود ندارد و در غیر این
    صورت تا ابد «در حال اجرا» می‌ماند.

    تبصره: این تابع (مثل خودِ ``job_manager``) فرض می‌کند سرور با یک ورکر واحد اجرا
    می‌شود. اگر روزی چند ورکر لازم شد، هم رجیستری job و هم این پاک‌سازی باید به یک
    store مشترک (مثلاً Redis) منتقل شوند.
    """
    stale = (
        session.query(WorkshopRun)
        .filter(WorkshopRun.status.in_(("pending", "running")))
        .all()
    )
    for run in stale:
        run.status = "error"
        run.finished_at = run.finished_at or utcnow()
        run.result_summary = {"error": "پردازش به دلیل راه‌اندازی مجدد سرور ناتمام ماند."}
    if stale:
        session.commit()
        logger.warning("marked %d orphaned run(s) as failed after restart", len(stale))
    return len(stale)
