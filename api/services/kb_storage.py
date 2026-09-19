"""محل واحد تصمیم‌گیری درباره‌ی «فایل‌های Chroma هر پایگاه‌دانش کجای دیسک هستند».

هیچ ماژول دیگری نباید مسیر یک پایگاه‌دانش را حدس بزند یا دستی بسازد -- همه از
همین دو تابع (``path_for`` و ``delete_kb_directory``) عبور می‌کنند، دقیقاً همان
الگویی که ``api/storage.py`` برای فایل‌های نتیجه به کار می‌برد.

ساختار دیسک::

    <vector_store_root>/
        <user_id>/
            <project_id>/
                <kb_id>/
                    manifest.json   ← {embedding_model, embedding_device, ...}
                    ...             ← فایل‌های خودِ Chroma (PersistentClient)

هر خواندن/نوشتن ابتدا مسیر را نسبت به ``vector_store_root`` resolve می‌کند و هر
مسیری که به بیرون از آن اشاره کند رد می‌شود (محافظت در برابر path traversal
حتی اگر ``user_id``/``project_id``/``kb_id`` به‌اشتباه از یک منبع نامطمئن آمده
باشند).
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from api.config import get_settings

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "manifest.json"

# فقط حروف/رقم/خط‌تیره/زیرخط مجازند -- شناسه‌های عددی (user_id/project_id) و
# UUID (kb_id) هر دو با این الگو مطابقت دارند؛ هر چیز دیگری رد می‌شود تا هرگز
# به یک قطعه‌ی مسیر غیرمنتظره (مثل "..") تبدیل نشود.
_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


class UnsafePathSegmentError(ValueError):
    """یکی از شناسه‌های مسیر (user_id/project_id/kb_id) نامعتبر است."""


def _segment(value: Any) -> str:
    text = str(value)
    if not text or not _SAFE_SEGMENT_RE.match(text):
        raise UnsafePathSegmentError(f"invalid path segment: {value!r}")
    return text


def vector_store_root() -> Path:
    root = Path(get_settings().vector_store_root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def models_cache_dir() -> Path:
    """پوشه‌ی مشترک کش مدل‌های embedding (بین همه‌ی پروژه‌ها/کاربران مشترک است)."""
    cache_dir = vector_store_root() / ".cache" / "models"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def path_for(user_id: int | str, project_id: int | str, kb_id: str) -> Path:
    """مسیر مطلقی پوشه‌ی یک پایگاه‌دانش را برمی‌گرداند (و پوشه‌های والد را می‌سازد)."""
    root = vector_store_root()
    directory = root / _segment(user_id) / _segment(project_id) / _segment(kb_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def relative_path_for(user_id: int | str, project_id: int | str, kb_id: str) -> str:
    """همان پوشه مثل path_for اما به صورت مسیر نسبی (نسبت به vector_store_root).

    این همان مقداری است که باید در knowledge_bases.chroma_persist_dir ذخیره شود -- دقیقاً مطابق همان
    الگوی که storage.py برای مسیر فایل نتیجه به کار می‌برد، تا جابه‌جایی پوشه‌ی داده‌ها
    ردیف‌های قبلی را خراب نکند.
    """
    root = vector_store_root()
    directory = path_for(user_id, project_id, kb_id)
    return directory.relative_to(root).as_posix()


def _resolve_within_root(directory: Path) -> Path | None:
    root = vector_store_root().resolve()
    try:
        resolved = directory.resolve()
    except OSError:  # pragma: no cover - مسیر نامعتبر در سطح سیستم‌عامل
        return None
    if not resolved.is_relative_to(root):
        logger.warning("refusing to touch a path outside vector_store_root: %r", directory)
        return None
    return resolved


def delete_kb_directory(user_id: int | str, project_id: int | str, kb_id: str) -> None:
    """پوشه‌ی یک پایگاه‌دانش را کامل پاک می‌کند -- ایمن و idempotent.

    اگر پوشه از قبل وجود نداشته باشد خطایی نمی‌دهد، و هرگز اجازه نمی‌دهد چیزی
    بیرون از ``vector_store_root`` پاک شود (حتی اگر شناسه‌ها دست‌کاری شده باشند).
    """
    try:
        root = vector_store_root()
        candidate = root / _segment(user_id) / _segment(project_id) / _segment(kb_id)
    except UnsafePathSegmentError:
        logger.warning(
            "refusing to delete KB directory with unsafe identifiers: user=%r project=%r kb=%r",
            user_id,
            project_id,
            kb_id,
        )
        return

    resolved = _resolve_within_root(candidate)
    if resolved is None:
        return
    if resolved.exists():
        shutil.rmtree(resolved, ignore_errors=True)
        logger.info(
            "knowledge base directory removed (user=%s, project=%s, kb=%s)",
            user_id,
            project_id,
            kb_id,
        )


def write_manifest(
    user_id: int | str,
    project_id: int | str,
    kb_id: str,
    *,
    embedding_model: str,
    embedding_device: str,
    chroma_collection_name: str,
    created_at: str,
) -> Path:
    """می‌نویسد ``manifest.json`` را کنار فایل‌های Chroma همان پایگاه‌دانش.

    این فایل عمداً با ردیف پایگاه‌داده تکراری (redundant) است: هدف این است که
    حتی با نگاه‌کردن مستقیم به پوشه (بدون دسترسی به پایگاه‌داده)، مدل embedding
    و دستگاه استفاده‌شده برای ساخت آن ایندکس همیشه قابل بازیابی باشد.
    """
    directory = path_for(user_id, project_id, kb_id)
    manifest_path = directory / MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(
            {
                "embedding_model": embedding_model,
                "embedding_device": embedding_device,
                "chroma_collection_name": chroma_collection_name,
                "created_at": created_at,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return manifest_path


def read_manifest(user_id: int | str, project_id: int | str, kb_id: str) -> dict[str, Any] | None:
    try:
        candidate = vector_store_root() / _segment(user_id) / _segment(project_id) / _segment(kb_id)
    except UnsafePathSegmentError:
        return None
    resolved = _resolve_within_root(candidate)
    if resolved is None:
        return None
    manifest_path = resolved / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("could not read manifest.json at %s", manifest_path)
        return None
