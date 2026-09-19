"""
مدیریت عمومی «job» های پس‌زمینه، مستقل از هر کارگاه (workspace) خاص.

این ماژول به‌طور خاص برای چک‌لیست حسابرسی نوشته نشده است: پیایپلاین چک‌لیست
(``pipeline.py``) و پایپلاین خلاصه‌سازی گزارش حسابرسی (``audit_pipeline.py``،
فاز ۲) هر دو از یک الگوی ساده و یکسان پیروی می‌کنند -- یک دیکشنری وضعیت با
کلیدهای ``status`` / ``stage`` / ``logs`` / ``result`` / ``error`` که توسط یک
ترد پس‌زمینه به‌صورت درجا (in-place) به‌روزرسانی می‌شود (نگاه کنید به
``pipeline.new_checklist_job`` و ``pipeline._set_stage``).

``JobManager`` صرفاً این دیکشنری‌ها را در حافظه نگه می‌دارد، به هرکدام یک
``job_id`` یکتا می‌دهد و امکان انقضا/پاک‌سازی خودکار را فراهم می‌کند -- بدون
هیچ فرضی درباره‌ی اینکه دیکشنری متعلق به کدام کارگاه است. به همین دلیل هم در
فاز ۱ (چک‌لیست) و هم در فاز ۲ (خلاصه‌سازی گزارش حسابرسی) قابل استفاده‌ی مجدد
است.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# وضعیت‌های «ناتمام» یک job -- همان واژگانی که لایه‌ی API در همه‌جا استفاده
# می‌کند (``api/schemas/common.py::JobStatus``). یک job در این وضعیت‌ها هنوز
# دارد می‌نویسد، بنابراین هر عملیات حذفی باید کنارش بایستد.
ACTIVE_JOB_STATUSES = ("pending", "running")


class JobManager:
    """رجیستری سبک و در-حافظه (in-memory) برای job های پس‌زمینه.

    هر job صرفاً یک ``dict[str, Any]`` است -- همان ساختاری که پایپلاین‌های
    موجود پروژه (``pipeline.py``/``audit_pipeline.py``) از قبل تولید و
    به‌روزرسانی می‌کنند. این کلاس هیچ فرضی درباره‌ی محتوای دیکشنری ندارد، جز
    اینکه انتظار دارد کلید ``status`` یکی از مقادیر
    ``pending`` / ``running`` / ``done`` / ``error`` باشد.

    از فاز داشبورد/پروژه‌ها به بعد، هر job علاوه بر ``job_id`` یکتای خودش با
    ``(project_id, workshop_type, run_id)`` هم برچسب‌گذاری می‌شود. این کلید
    مرکب همان چیزی است که اجازه می‌دهد چند کارگاه در یک پروژه (و چند پروژه
    به‌طور هم‌زمان) بدون هیچ نشتی بین حالت‌ها اجرا شوند: هر job دیکشنری مستقل
    خودش را دارد و وضعیت هیچ‌کدام روی دیگری اثر نمی‌گذارد.
    """

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.ttl_seconds = ttl_seconds

    # ------------------------------------------------------------------
    # ثبت / بازیابی
    # ------------------------------------------------------------------
    def create(
        self,
        factory: Callable[[], dict[str, Any]],
        *,
        workdir: Optional[Path] = None,
        kind: str = "generic",
        project_id: Optional[int] = None,
        run_id: Optional[int] = None,
        on_finish: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> tuple[str, dict[str, Any]]:
        """یک job جدید می‌سازد و آن را ثبت می‌کند. ``factory`` معمولا یکی از
        ``pipeline.new_checklist_job`` یا معادل آن در audit_pipeline.py است.

        ``on_finish`` یک callback اختیاری است که درست پس از رسیدن job به وضعیت
        نهایی (``done`` یا ``error``) و در ترد نگهبان فراخوانی می‌شود -- همان‌جایی
        که لایه‌ی کارگاه‌ها ردیف تاریخچه‌ی ``workshop_runs`` و فایل نتیجه را ثبت
        می‌کند (نگاه کنید به ``api/workshops/runs.py``). این callback عمداً در
        ``pipeline.py``/``audit_pipeline.py`` صدا زده نمی‌شود تا آن دو فایل
        دست‌نخورده بمانند.
        """
        job_id = uuid.uuid4().hex
        job = factory()
        # در pipeline.py وضعیت اولیه "idle" نامیده می‌شود؛ لایهی API فقط از واژگان
        # pending/running/done/error استفاده می‌کند، به همین دلیل اینجا نرمالایزی می‌شود.
        job["status"] = "pending"
        job["_job_id"] = job_id
        job["_kind"] = kind
        job["_workdir"] = workdir
        job["_created_at"] = time.time()
        job["_project_id"] = project_id
        job["_run_id"] = run_id
        job["_on_finish"] = on_finish
        with self._lock:
            self._jobs[job_id] = job
        logger.info(
            "job %s created (kind=%s, project=%s, run=%s)", job_id, kind, project_id, run_id
        )
        return job_id, job

    def get(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            return self._jobs.get(job_id)

    def get_by_run(self, run_id: int) -> Optional[dict[str, Any]]:
        """job زنده‌ی متناظر با یک ردیف ``workshop_runs`` (اگر هنوز در حافظه باشد)."""
        with self._lock:
            for job in self._jobs.values():
                if job.get("_run_id") == run_id:
                    return job
        return None

    def list_for_project(self, project_id: int) -> list[dict[str, Any]]:
        """همه‌ی job های یک پروژه (مناسب برای پنل «کارهای در جریان»)."""
        with self._lock:
            return [job for job in self._jobs.values() if job.get("_project_id") == project_id]

    def has_active_job_for_project(self, project_id: int) -> bool:
        """آیا این پروژه همین حالا یک job ناتمام (``pending``/``running``) دارد؟

        برای عملیات‌هایی که با یک اجرای در جریان نمی‌توانند هم‌زیستی کنند لازم
        است -- مشخصاً حذف کامل پروژه: اگر یک ایندکس‌سازی/تحلیل همین حالا روی
        فایل‌های این پروژه در حال نوشتن باشد، حذف هم‌زمان می‌تواند داده‌ای را
        نیمه‌پاک کند یا ردیف/فایل یتیم باقی بگذارد. بنابراین حذف باید تا پایان
        آن اجرا *رد* شود، نه اینکه با آن مسابقه دهد.

        مقایسه با ``ACTIVE_JOB_STATUSES`` انجام می‌شود، نه با نبودِ ``done``:
        یک job در وضعیت ``error`` تمام‌شده است و جلوی حذف را نمی‌گیرد.
        """
        return any(
            job.get("status") in ACTIVE_JOB_STATUSES
            for job in self.list_for_project(project_id)
        )

    def delete(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job is not None:
            logger.info("job %s removed from registry", job_id)

    def delete_for_project(self, project_id: int) -> int:
        """حذف همه‌ی job های یک پروژه (هنگام حذف خودِ پروژه)."""
        with self._lock:
            doomed = [
                job_id
                for job_id, job in self._jobs.items()
                if job.get("_project_id") == project_id
            ]
            for job_id in doomed:
                self._jobs.pop(job_id, None)
        if doomed:
            logger.info("removed %d job(s) of project %s", len(doomed), project_id)
        return len(doomed)

    # ------------------------------------------------------------------
    # لاگ‌گیری چرخه‌ی حیات job (created/running/done/error)
    # ------------------------------------------------------------------
    def watch_lifecycle(self, job_id: str, job: dict[str, Any]) -> None:
        """یک ترد سبک راه می‌اندازد که تغییرات ``job["status"]`` را دنبال و
        در لاگ ثبت می‌کند -- بدون این‌که به ``pipeline.py``/``audit_pipeline.py``
        (که ترد پردازش واقعی را اجرا می‌کنند و خارج از محدوده‌ی این فاز هستند)
        دست بزند. با این کار هر دو کارگاه (چک‌لیست و خلاصه‌سازی گزارش) به‌طور
        یکسان رویدادهای running/done/error را در لاگ ثبت می‌کنند، مکمل رویداد
        «created» که همین‌جا در ``create()`` ثبت می‌شود.

        پس از رسیدن job به وضعیت نهایی، callback ``on_finish`` (که در ``create``
        ثبت شده) یک بار فراخوانی می‌شود.
        """
        kind = job.get("_kind", "generic")
        on_finish = job.get("_on_finish")

        def _watch() -> None:
            last_status = job.get("status")
            while last_status not in ("done", "error"):
                time.sleep(0.3)
                status = job.get("status")
                if status != last_status:
                    logger.info("job %s (kind=%s) status changed: %s -> %s", job_id, kind, last_status, status)
                    last_status = status

            if last_status == "error":
                logger.error("job %s (kind=%s) failed: %s", job_id, kind, job.get("error"))
            else:
                logger.info("job %s (kind=%s) completed successfully", job_id, kind)

            if on_finish is not None:
                try:
                    on_finish(job)
                except Exception:  # noqa: BLE001 -- ثبت تاریخچه نباید ترد نگهبان را بکشد
                    logger.exception("on_finish callback failed for job %s", job_id)

        threading.Thread(target=_watch, name=f"job-watch-{job_id}", daemon=True).start()

    # ------------------------------------------------------------------
    # پاک‌سازی job های منقضی‌شده (فراخوانی دوره‌ای، مثلا هنگام هر درخواست
    # وضعیت یا از طریق یک تسک زمان‌بندی‌شده)
    # ------------------------------------------------------------------
    def purge_expired(self) -> None:
        now = time.time()
        with self._lock:
            expired = [
                job_id
                for job_id, job in self._jobs.items()
                if job.get("status") in ("done", "error")
                and now - job.get("_created_at", now) > self.ttl_seconds
            ]
            for job_id in expired:
                self._jobs.pop(job_id, None)
        for job_id in expired:
            logger.info("job %s expired and was purged", job_id)


# نمونه‌ی یکتا (singleton) در سطح ماژول -- کافی برای یک فرآیند uvicorn تک-ورکر.
# اگر در آینده اجرای چند-ورکر لازم شد، این باید با یک store خارجی (Redis و...)
# جایگزین شود؛ رابط ``JobManager`` عمداً کوچک نگه داشته شده تا آن تغییر ساده باشد.
job_manager = JobManager()
