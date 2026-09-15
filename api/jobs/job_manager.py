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


class JobManager:
    """رجیستری سبک و در-حافظه (in-memory) برای job های پس‌زمینه.

    هر job صرفاً یک ``dict[str, Any]`` است -- همان ساختاری که پایپلاین‌های
    موجود پروژه (``pipeline.py``/``audit_pipeline.py``) از قبل تولید و
    به‌روزرسانی می‌کنند. این کلاس هیچ فرضی درباره‌ی محتوای دیکشنری ندارد، جز
    اینکه انتظار دارد کلید ``status`` یکی از مقادیر
    ``pending`` / ``running`` / ``done`` / ``error`` باشد.
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
    ) -> tuple[str, dict[str, Any]]:
        """یک job جدید می‌سازد و آن را ثبت می‌کند. ``factory`` معمولا یکی از
        ``pipeline.new_checklist_job`` یا معادل آن در audit_pipeline.py است."""
        job_id = uuid.uuid4().hex
        job = factory()
        # در pipeline.py وضعیت اولیه "idle" نامیده می‌شود؛ لایهی API فقط از واژگان
        # pending/running/done/error استفاده می‌کند، به همین دلیل اینجا نرمالایزی می‌شود.
        job["status"] = "pending"
        job["_job_id"] = job_id
        job["_kind"] = kind
        job["_workdir"] = workdir
        job["_created_at"] = time.time()
        with self._lock:
            self._jobs[job_id] = job
        logger.info("job %s created (kind=%s)", job_id, kind)
        return job_id, job

    def get(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            return self._jobs.get(job_id)

    def delete(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job is not None:
            logger.info("job %s removed from registry", job_id)

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
        """
        kind = job.get("_kind", "generic")

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
