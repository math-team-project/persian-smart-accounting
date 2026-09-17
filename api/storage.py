"""ذخیره‌سازی فایل‌های نتیجه روی دیسک.

قاعده‌ی این فاز: از هر اجرا فقط **خروجی** نگه داشته می‌شود (مثلاً گزارش Word)؛
فایل‌های ورودی کاربر همچنان مثل قبل پس از پایان پردازش حذف می‌شوند
(``pipeline.cleanup_workdir`` / ``audit_pipeline.cleanup_audit_workdir``).

ساختار دیسک::

    <results_dir>/
        پروژه-<project_id>/          ← با حذف پروژه، کل این پوشه پاک می‌شود
            run-<run_id>-<نام امن>.docx

مسیر ذخیره‌شده در پایگاه‌داده **نسبی** است (نسبت به ``results_dir``) تا جابه‌جا
کردن پوشه‌ی داده‌ها ردیف‌های قبلی را خراب نکند. هر خواندن از دیسک از
``resolve`` عبور می‌کند و مسیرهایی که به بیرون از ``results_dir`` اشاره کنند
رد می‌شوند (تا یک ردیف دست‌کاری‌شده نتواند فایل دلخواه سرور را بخواند/پاک کند).
"""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from api.config import get_settings

logger = logging.getLogger(__name__)

_UNSAFE_CHARS_RE = re.compile(r"[^\w\u0600-\u06FF.\-]+")


def results_root() -> Path:
    root = Path(get_settings().results_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def project_dir(project_id: int) -> Path:
    return results_root() / f"project-{project_id}"


def _safe_filename(name: str) -> str:
    """نام فایل را برای ذخیره روی دیسک امن می‌کند (نام اصلی برای دانلود حفظ می‌شود)."""
    candidate = _UNSAFE_CHARS_RE.sub("_", Path(name).name).strip("._") or "result"
    return candidate[:120]


def save_result_file(
    project_id: int, run_id: int, filename: str, content: bytes
) -> tuple[str, Path]:
    """فایل نتیجه را ذخیره می‌کند و ``(مسیر نسبی, مسیر مطلق)`` برمی‌گرداند."""
    directory = project_dir(project_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"run-{run_id}-{_safe_filename(filename)}"
    path.write_bytes(content)

    relative = path.relative_to(results_root()).as_posix()
    logger.info("result file saved for project=%s run=%s (%s bytes)", project_id, run_id, len(content))
    return relative, path


def resolve(relative_path: str) -> Path | None:
    """مسیر نسبی ذخیره‌شده در پایگاه‌داده را به مسیر مطلق امن تبدیل می‌کند."""
    root = results_root().resolve()
    try:
        path = (root / relative_path).resolve()
    except OSError:  # pragma: no cover - مسیر نامعتبر
        return None
    if not path.is_relative_to(root):
        logger.warning("refusing to resolve result path outside results dir: %r", relative_path)
        return None
    return path


def read_result_file(relative_path: str) -> bytes | None:
    path = resolve(relative_path)
    if path is None or not path.is_file():
        return None
    return path.read_bytes()


def delete_result_file(relative_path: str) -> None:
    path = resolve(relative_path)
    if path is not None and path.is_file():
        path.unlink(missing_ok=True)


def delete_project_files(project_id: int) -> None:
    """کل پوشه‌ی نتیجه‌های یک پروژه را پاک می‌کند (شامل فایل‌های یتیم احتمالی)."""
    directory = project_dir(project_id)
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)
        logger.info("result files removed for project=%s", project_id)
