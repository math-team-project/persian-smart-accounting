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
                    chunks.jsonl    ← متن کامل هر chunk (نگاه کنید به ``append_chunk``)
                    ...             ← فایل‌های خودِ Chroma (PersistentClient)

هر خواندن/نوشتن ابتدا مسیر را نسبت به ``vector_store_root`` resolve می‌کند و هر
مسیری که به بیرون از آن اشاره کند رد می‌شود (محافظت در برابر path traversal
حتی اگر ``user_id``/``project_id``/``kb_id`` به‌اشتباه از یک منبع نامطمئن آمده
باشند).

**چرا ``chunks.jsonl`` لازم است؟** ``rag_chat_module`` در ``ChromaVectorStore.add``
فقط بردار و یک متادیتای کوچک (کلید فایل/نام شیت) را در Chroma می‌نویسد و متن
جدول‌ها را در یک کش **درون-فرایندی** (``_chunk_cache``) نگه می‌دارد؛ Chroma هم
هیچ ``documents``ی دریافت نمی‌کند. یعنی یک فرایند تازه (مثل درخواست چت‌بات در
اجرای بعدی سرور) چیزی برای جست‌وجو ندارد و ``search`` همیشه خالی برمی‌گردد. چون
پکیج ``rag_chat_module`` طبق طراحی دست‌نخورده می‌ماند، داشبورد *خودش* متن هر
chunk را کنار همان پایگاه‌دانش ذخیره می‌کند تا مسیر «فقط-خواندنی» چت‌بات بتواند
کش را بازسازی کند (نگاه کنید به
``api/services/rag_ai_adapter.py::build_readonly_knowledge_base``).
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any, Iterable

from api.config import get_settings

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "manifest.json"
CHUNKS_FILENAME = "chunks.jsonl"

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


# ---------------------------------------------------------------------------
# متن کامل chunk ها (sidecar) -- «آنچه Chroma نگه نمی‌دارد»
# ---------------------------------------------------------------------------
def chunks_path_for(user_id: int | str, project_id: int | str, kb_id: str) -> Path:
    """مسیر فایل ``chunks.jsonl`` یک پایگاه‌دانش (پوشه‌های والد ساخته می‌شوند)."""
    return path_for(user_id, project_id, kb_id) / CHUNKS_FILENAME


def serialize_chunk(chunk: Any) -> dict[str, Any]:
    """یک ``Chunk`` پکیج ``rag_chat_module`` را به یک دیکشنری قابل-JSON تبدیل می‌کند.

    ``dataframe`` با ``orient="split"`` ذخیره می‌شود (ستون‌ها/اندیس/داده) تا
    هنگام بازخوانی دقیقاً همان جدول -- با همان ترتیب سطر و ستون -- بازسازی شود.
    ``default=str`` تضمین می‌کند مقادیری مثل ``Timestamp`` یا نام ستون غیررشته‌ای
    کل نوشتن را شکست ندهند (به متن تبدیل می‌شوند، دقیقاً همان چیزی که مدل هم
    می‌دید).
    """
    frame = chunk.dataframe
    return {
        "chunk_id": str(chunk.chunk_id),
        "file_key": str(chunk.file_key),
        "source_sheet_names": [str(name) for name in chunk.source_sheet_names],
        "metadata_text": str(chunk.metadata_text or ""),
        "dataframe": frame.to_dict(orient="split"),
    }


def append_chunks(
    user_id: int | str,
    project_id: int | str,
    kb_id: str,
    chunks: Iterable[Any],
    *,
    state: dict[str, set[str]] | None = None,
) -> int:
    """متن chunk ها را به ``chunks.jsonl`` همان پایگاه‌دانش اضافه می‌کند.

    نوشتن **افزودنی** است (نه بازنویسی): یک پایگاه‌دانش در چند مرحله ساخته
    می‌شود (ابتدا فایل‌های آپلودی، بعد نتیجه‌ی تطبیق‌شده‌ی چک‌لیست) و هیچ‌کدام
    نباید chunk های مرحله‌ی قبلی را از بین ببرد.

    ``chunk_id`` تکراری نوشته نمی‌شود: ``KnowledgeBase.index_files`` برای هر
    فراخوانی یک ``Chunk`` تازه می‌سازد و اگر یک فایل دوباره ایندکس شود، همان
    شناسه‌ها دوباره تولید می‌شوند -- بدون این محافظ، مسیر فقط-خواندنی همان جدول
    را چند بار برمی‌گرداند. ``state`` یک مجموعه‌ی شناسه‌های نوشته‌شده است که بین
    فراخوانی‌ها نگه داشته می‌شود (اولین بار از خود فایل پر می‌شود).

    خروجی: تعداد رکوردهای تازه‌نوشته‌شده. هر خطای I/O فقط لاگ می‌شود -- نبود
    این فایل یک قابلیت (چت‌بات) را کم می‌کند، نه خودِ کارگاه چک‌لیست را.
    """
    if state is not None and "ids" not in state:
        state["ids"] = {record["chunk_id"] for record in read_chunk_records(user_id, project_id, kb_id)}

    try:
        path = chunks_path_for(user_id, project_id, kb_id)
        written = 0
        with path.open("a", encoding="utf-8") as handle:
            for chunk in chunks:
                chunk_id = str(chunk.chunk_id)
                if state is not None and chunk_id in state["ids"]:
                    continue
                handle.write(
                    json.dumps(serialize_chunk(chunk), ensure_ascii=False, default=str) + "\n"
                )
                if state is not None:
                    state["ids"].add(chunk_id)
                written += 1
        return written
    except Exception:  # noqa: BLE001 -- نوشتن sidecar هرگز نباید اجرای اصلی را بشکند
        logger.exception("could not persist chunk text for kb=%s", kb_id)
        return 0


def read_chunk_records(
    user_id: int | str, project_id: int | str, kb_id: str
) -> list[dict[str, Any]]:
    """رکوردهای خام ``chunks.jsonl`` (بدون بازسازی DataFrame).

    خط‌های ناقص/خراب نادیده گرفته می‌شوند (مثلاً اگر فرایند وسط نوشتن قطع شده
    باشد) تا یک خط خراب کل مسیر چت را از کار نیندازد.
    """
    try:
        candidate = (
            vector_store_root() / _segment(user_id) / _segment(project_id) / _segment(kb_id)
        )
    except UnsafePathSegmentError:
        return []
    resolved = _resolve_within_root(candidate)
    if resolved is None:
        return []
    path = resolved / CHUNKS_FILENAME
    if not path.is_file():
        return []

    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("skipping a malformed line in %s", path)
                    continue
                if isinstance(payload, dict) and "chunk_id" in payload:
                    records.append(payload)
    except OSError:
        logger.warning("could not read chunk text at %s", path)
        return []
    return records


def read_chunks(user_id: int | str, project_id: int | str, kb_id: str) -> list[Any]:
    """chunk های ذخیره‌شده را به‌صورت اشیای ``Chunk`` پکیج ``rag_chat_module`` برمی‌گرداند.

    ایمپورت ``Chunk`` عمداً داخل تابع و تنبل است: این ماژول نباید در زمان ایمپورت
    به ``rag_chat_module``/``pandas`` وابسته شود (بقیه‌ی توابعش فقط مسیر می‌سازند).
    خط‌هایی که DataFrameشان بازسازی نمی‌شود نادیده گرفته می‌شوند.
    """
    from llm_variable_resolver.retrieval import Chunk  # ایمپورت تنبل (نگاه کنید به docstring)
    import pandas as pd

    chunks: list[Any] = []
    for record in read_chunk_records(user_id, project_id, kb_id):
        try:
            frame_payload = record.get("dataframe") or {}
            frame = pd.DataFrame(
                data=frame_payload.get("data", []),
                columns=frame_payload.get("columns", None),
                index=frame_payload.get("index", None),
            )
            chunks.append(
                Chunk(
                    chunk_id=str(record["chunk_id"]),
                    file_key=str(record.get("file_key", "")),
                    source_sheet_names=[str(n) for n in record.get("source_sheet_names", [])],
                    dataframe=frame,
                    metadata_text=str(record.get("metadata_text", "")),
                )
            )
        except Exception:  # noqa: BLE001 -- یک رکورد خراب نباید کل بازخوانی را بشکند
            logger.warning("skipping an unreadable chunk record in kb=%s", kb_id, exc_info=True)
    return chunks


def has_chunk_text(user_id: int | str, project_id: int | str, kb_id: str) -> bool:
    """آیا متن قابل‌جست‌وجوی این پایگاه‌دانش ذخیره شده است؟"""
    return bool(read_chunk_records(user_id, project_id, kb_id))
