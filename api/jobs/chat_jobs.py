"""اجراکننده‌ی سبک «پرسش چت‌بات» در پس‌زمینه -- عمداً جدا از ``JobManager``.

هر پرسش چت‌بات یک واحد کار مستقل است: «این یک پرسش را از پایگاه‌دانش بگیر و
پاسخش را روی *همین یک ردیف پیام* بنویس». ``JobManager`` برای مدل دیگری ساخته شده
است (ایندکس‌سازی/تحلیل چندفایلی با ``workshop_runs``، فایل نتیجه، پنل «کارهای در
جریان» و انقضای TTL) و دو چیز در آن با این واحد کار جور درنمی‌آید:

۱) **کلید job.** ``JobManager`` به هر job یک ``job_id`` تصادفی می‌دهد و هیچ راهی
   برای «پیدا کردن job با یک کلید ترکیبی دلخواه» ندارد. این‌جا اما کلید باید
   دقیقاً ``(project_id, session_id, assistant_message_id)`` باشد تا وضعیت/تکرار
   یک پرسش هرگز بین پروژه‌ها یا گفتگوهای مختلف قاطی نشود. بنابراین همان کلید،
   کلید دیکشنری این رجیستری است (نه یک شناسه‌ی جدا).

۲) **معنای «پروژه مشغول است».** ``has_active_job_for_project`` حذف پروژه را تا
   پایان هر job فعال رد می‌کند. یک پرسش چت‌باتی که کاربر ممکن است ده‌ها ثانیه بعد
   هم پاسخ نگیرد نباید پروژه را «قفل» کند (و نباید در پنل کارهای در جریان، که بر
   پایه‌ی ``workshop_runs`` است، ظاهر شود -- و نمی‌شود، چون این کارگاه هیچ ردیف
   اجرایی ندارد). پس این رجیستری هیچ اثری روی آن مسیرها ندارد.

تضمین‌های ایمنی دقیقاً همان‌هایی است که ``JobManager`` در بقیه‌ی کارگاه‌ها می‌دهد:

* **جداسازی کامل:** کلید هر job سه‌گانه‌ی ``(project_id, session_id,
  assistant_message_id)`` است؛ هیچ جست‌وجو/تکرار/گزارش وضعیتی نمی‌تواند از مرز
  پروژه/گفتگو عبور کند.
* **بدون قاطی‌شدن:** هر پرسش ترد خودش را دارد (بدون صف) -- یک پرسش کند در یک
  گفتگو، پرسش هم‌زمانِ گفتگویی دیگر را نه بلاک می‌کند و نه کند.
* **شکست یک job سرور را نمی‌اندازد:** هر استثنا در ترد پس‌زمینه گرفته و لاگ
  می‌شود؛ ثبت وضعیت ``failed`` روی ردیف پیام کار خودِ تابع کاری است (نگاه کنید به
  ``api/services/financial_chatbot_service.py::answer_question``).
* **پاک‌سازی:** ترد daemon است و پس از پایان، وضعیت پایانی‌اش در همین رجیستری
  می‌ماند تا یک بررسی وضعیت دیرآمده جواب بگیرد؛ ردیف‌های تمام‌شده‌ی قدیمی هنگام
  هر ثبت تازه هرس می‌شوند.

**عمداً هیچ متد cancel ای وجود ندارد.** خواسته‌ی محصول این است که پیمایش کاربر
(بستن صفحه، رفتن به کارگاه دیگر، عوض‌کردن گفتگو) *هرگز* کار سمت سرور را نکشد؛
تنها کلاینت polling را متوقف می‌کند. کلید ترکیبی این‌جا فقط برای جداسازی و
گزارش وضعیت است، نه برای لغو.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# کلید یکتای هر پرسش: (project_id, session_id, assistant_message_id).
ChatAskKey = tuple[int, str, str]

_STATUS_RUNNING = "running"
_STATUS_DONE = "done"
_STATUS_ERROR = "error"


class ChatAskJobRegistry:
    """رجیستری در-حافظه‌ی پرسش‌های چت‌بات، کلیدگذاری‌شده با ``ChatAskKey``."""

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._jobs: dict[ChatAskKey, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.ttl_seconds = ttl_seconds

    # ------------------------------------------------------------------
    # ثبت
    # ------------------------------------------------------------------
    def submit(self, key: ChatAskKey, work: Callable[[], None]) -> bool:
        """کار را در یک ترد daemon تازه اجرا می‌کند. ``False`` یعنی همین کلید
        از قبل در جریان است (پس کار دوباره شروع نمی‌شود).

        ترد **بیرون از قفل** استارت می‌شود تا نگه‌داشتن قفل، کارِ ترد را بلاک نکند.
        """
        with self._lock:
            if key in self._jobs:
                logger.warning("chat ask job %s is already running; not starting a second one", key)
                return False
            self._jobs[key] = {
                "status": _STATUS_RUNNING,
                "error": None,
                "started_at": time.time(),
                "finished_at": None,
            }
        self.prune()
        threading.Thread(
            target=self._run, args=(key, work), name=f"chat-ask-{key[1][:8]}-{key[2][:8]}", daemon=True
        ).start()
        return True

    def _run(self, key: ChatAskKey, work: Callable[[], None]) -> None:
        """اجرای کار؛ هیچ استثنایی از این ترد بیرون نمی‌زند."""
        try:
            work()
        except Exception as exc:  # noqa: BLE001 -- شکست یک پرسش نباید سرور را بیندازد
            logger.exception("chat ask job %s failed", key)
            self._finish(key, _STATUS_ERROR, str(exc) or exc.__class__.__name__)
        else:
            self._finish(key, _STATUS_DONE, None)

    def _finish(self, key: ChatAskKey, status: str, error: Optional[str]) -> None:
        with self._lock:
            job = self._jobs.get(key)
            if job is not None:
                job["status"] = status
                job["error"] = error
                job["finished_at"] = time.time()
        logger.info("chat ask job %s finished with status=%s", key, status)

    # ------------------------------------------------------------------
    # بازیابی / پاک‌سازی
    # ------------------------------------------------------------------
    def get(self, key: ChatAskKey) -> Optional[dict[str, Any]]:
        """وضعیت یک پرسش با *کلید کامل* -- هرگز با یک شناسه‌ی تنها."""
        with self._lock:
            job = self._jobs.get(key)
            return dict(job) if job is not None else None

    def is_running(self, key: ChatAskKey) -> bool:
        job = self.get(key)
        return bool(job and job["status"] == _STATUS_RUNNING)

    def delete(self, key: ChatAskKey) -> None:
        with self._lock:
            self._jobs.pop(key, None)

    def prune(self) -> None:
        """حذف ردیف‌های تمام‌شده‌ی قدیمی (تا این دیکشنری بی‌نهایت رشد نکند)."""
        now = time.time()
        with self._lock:
            expired = [
                key
                for key, job in self._jobs.items()
                if job["status"] != _STATUS_RUNNING
                and now - (job.get("finished_at") or job.get("started_at", now)) > self.ttl_seconds
            ]
            for key in expired:
                self._jobs.pop(key, None)


# نمونه‌ی یکتا (singleton) در سطح ماژول -- مثل ``job_manager``، کافی برای یک
# فرآیند uvicorn تک-ورکر. چند-ورکرِ آینده باید این را هم به یک store مشترک ببرد.
chat_ask_jobs = ChatAskJobRegistry()
