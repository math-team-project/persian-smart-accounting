"""کسب‌وکار حذف پروژه: پایگاه‌داده + فایل‌های نتیجه + پایگاه‌دانش + job های در حافظه.

«حذف پروژه» باید یک عمل کامل و بدون باقی‌مانده باشد: هیچ ردیف یتیمی در
پایگاه‌داده و هیچ فایل یتیمی روی دیسک باقی نماند. این تابع همان ترتیبی را اجرا
می‌کند که این تضمین را می‌دهد:

    ۰) اگر همان پروژه یک job ناتمام دارد، حذف **رد** می‌شود
       (``ProjectDeletionBlockedError``): حذف هم‌زمان با یک ایندکس‌سازی/تحلیلِ
       در جریان می‌تواند داده را نیمه‌پاک کند، پس تا پایان آن اجرا صبر می‌کنیم
       (نگاه کنید به ``JobManager.has_active_job_for_project``).
    ۱) job های در حافظه‌ی همان پروژه از رجیستری خارج می‌شوند (تا هیچ endpoint ای
       بعد از حذف، وضعیت پروژه‌ی حذف‌شده را برنگرداند؛ ترد پردازش در پس‌زمینه
       مستقل است و کار خودش را تمام می‌کند، اما چون ردیف تاریخچه دیگر وجود ندارد،
       ثبت نتیجه‌اش بی‌اثر و بی‌خطر است -- ``finalize_run`` ردیف غایب را نادیده
       می‌گیرد)،
    ۲) پیام‌های گفتگوهای چت‌بات همین پروژه حذف می‌شوند و سپس خودِ گفتگوها
       (پیام‌ها فرزند گفتگو هستند، پس ترتیب مهم است)،
    ۳) پوشه‌های روی-دیسک پایگاه‌دانش‌های همین پروژه -- و سپس خودِ پوشه‌ی والدِ
       پروژه اگر خالی ماند -- پاک می‌شوند **پیش از** حذف ردیف‌های پایگاه‌داده،
       چون فهرست دقیق شناسه‌ها از همان ردیف‌ها می‌آید و نمی‌خواهیم پس از حذف
       ردیف‌ها مجبور شویم مسیرها را حدس بزنیم,
    ۴) ردیف‌های ``knowledge_bases`` و ``kb_files`` همین پروژه حذف می‌شوند (اشاره‌گر
       ``projects.latest_ready_kb_id`` پیش از آن پاک می‌شود تا هیچ‌گاه به ردیفی
       که در حال حذف است اشاره نکند)،
    ۵) ردیف‌های ``workshop_runs`` و ``workshop_settings`` و خود پروژه حذف می‌شوند،
    ۶) کل پوشه‌ی نتیجه‌های همان پروژه روی دیسک پاک می‌شود (این کار حتی فایل‌های
       یتیم احتمالی را هم از بین می‌برد، نه فقط فایل‌های شناخته‌شده در پایگاه‌داده).

هر سه مرحله‌ی جدید (۲ تا ۴) توابعی با نام صریح و دامنه‌ی «کل پروژه» هستند و
هرگز از هیچ endpoint ای صدا زده نمی‌شوند -- تنها مسیر اجرای‌شان همین تابع است،
پس در کل برنامه دقیقاً یک مسیر کد می‌تواند یک پروژه را کامل پاک کند.

سیاست خطا عیناً همان چیزی است که از قبل برای فایل‌های نتیجه وجود داشت: حذف
روی-دیسک بهترین‌تلاش است (خطای یک فایل/پوشه عملیات را متوقف نمی‌کند)، اما خطای
پایگاه‌داده (مثل ``projects_repo.delete``) مثل قبل کل عملیات را شکست می‌دهد.

برگشت‌پذیر نیست -- به همین دلیل UI یک مرحله‌ی تأیید صریح دارد.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from api import storage
from api.db.models import Project
from api.jobs.job_manager import job_manager
from api.repositories import chat_messages as chat_messages_repo
from api.repositories import chat_sessions as chat_sessions_repo
from api.repositories import knowledge_bases as kb_repo
from api.repositories import projects as projects_repo
from api.services import kb_storage

logger = logging.getLogger(__name__)

# پیام فارسیِ ردِ حذف وقتی یک پردازش در جریان برای همان پروژه وجود دارد. لحن آن
# با بقیه‌ی پیام‌های قابل‌نمایش همین برنامه یکی است: کوتاه، بدون اصطلاح فنی و
# همراه با «چه کاری انجام دهید».
ACTIVE_JOB_BLOCK_MESSAGE_FA = (
    "این پروژه در حال حاضر یک پردازش در جریان دارد. برای حذف پروژه، ابتدا صبر "
    "کنید آن پردازش تمام شود و سپس دوباره تلاش کنید."
)


class ProjectDeletionBlockedError(Exception):
    """حذف پروژه انجام نشد، چون همان پروژه یک پردازش در جریان دارد.

    پیام این استثنا از قبل فارسی و آماده‌ی نمایش به کاربر است (روتر آن را به
    یک پاسخ 409 با همان متن تبدیل می‌کند).
    """


def delete_project(session: Session, project: Project, *, manager=job_manager) -> dict[str, int]:
    """پروژه را همراه با *همه‌ی* داده‌های وابسته‌اش (پایگاه‌داده + دیسک) پاک می‌کند.

    خروجی یک دیکشنری شمارشی برای لاگ است. این تابع هیچ‌گاه روی «همه‌ی پروژه‌ها»
    کار نمی‌کند: هر مرحله صریحاً با ``project_id`` (و ``user_id`` برای ساخت مسیر
    دیسک) محدود می‌شود.

    Raises:
        ProjectDeletionBlockedError: یک job ناتمام برای همین پروژه در جریان است.
    """
    project_id = project.id
    user_id = project.user_id

    # مرحله‌ی ۰ -- پیش از هر حذفی. این بررسی باید *اولین* کار باشد تا در حالت رد،
    # هیچ‌چیز (نه job، نه ردیف، نه فایل) تغییر نکرده باشد.
    if manager.has_active_job_for_project(project_id):
        logger.warning(
            "project %s deletion refused: an active job is still running", project_id
        )
        raise ProjectDeletionBlockedError(ACTIVE_JOB_BLOCK_MESSAGE_FA)

    # مرحله‌ی ۱
    removed_jobs = manager.delete_for_project(project_id)

    # مرحله‌ی ۲ -- پیام‌های گفتگوها **پیش از** خودِ گفتگوها (پیام فرزند گفتگوست).
    # هر دو حذف صریح‌اند، با همان کلید سه‌گانه‌ی (session/project/user) و بدون هیچ
    # تکیه‌ای بر cascade سطح پایگاه‌داده.
    removed_chat_messages = chat_messages_repo.delete_all_chat_messages_for_project(
        session, project_id, user_id
    )
    removed_chat_sessions = chat_sessions_repo.delete_all_chat_sessions_for_project(
        session, project_id, user_id
    )

    # مرحله‌ی ۳ -- پیش از حذف ردیف‌های پایگاه‌دانش: فهرست دقیق شناسه‌ها همان لحظه
    # از پایگاه‌داده خوانده می‌شود، پس هیچ مسیری روی دیسک حدس زده نمی‌شود.
    targeted_kb_directories = kb_storage.delete_vector_store_directories_for_project(
        user_id, project_id
    )

    # مرحله‌ی ۴
    removed_kbs = kb_repo.delete_all_knowledge_bases_for_project(session, project_id, user_id)

    # مرحله‌ی ۵
    summary = projects_repo.delete(session, project)

    # مرحله‌ی ۶ -- بهترین‌تلاش، مثل قبل (یک پوشه‌ی ناقص نباید حذف پروژه را بشکند).
    paths = summary["result_paths"]
    storage.delete_project_files(project_id)

    logger.info(
        "project %s deleted (jobs=%d, chat_messages=%d, chat_sessions=%d, kb_directories=%d, "
        "knowledge_bases=%d, kb_files=%d, runs=%d, settings=%d, result_files=%d)",
        project_id,
        removed_jobs,
        removed_chat_messages,
        removed_chat_sessions,
        targeted_kb_directories,
        removed_kbs["knowledge_bases"],
        removed_kbs["kb_files"],
        summary["runs"],
        summary["settings"],
        len(paths),
    )
    return {
        "jobs": removed_jobs,
        "chat_messages": removed_chat_messages,
        "chat_sessions": removed_chat_sessions,
        "kb_directories": targeted_kb_directories,
        "knowledge_bases": removed_kbs["knowledge_bases"],
        "kb_files": removed_kbs["kb_files"],
        "runs": summary["runs"],
        "settings": summary["settings"],
        "result_files": len(paths),
    }
