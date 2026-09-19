"""پرس‌وجوهای پایگاه‌دانش (Knowledge Base) هر پروژه.

سبک این ماژول دقیقاً موازی ``api/repositories/workshop_runs.py`` است: هر تابع
یک عملیات کوچک و صریح روی پایگاه‌داده انجام می‌دهد و ``session.commit()`` را
خودش صدا می‌زند (چون این توابع از لایه‌ی سرویس/ترد پس‌زمینه فراخوانی می‌شوند، نه
از داخل یک تراکنش بزرگ‌تر مشترک).

نکته‌ی جداسازی (isolation) مهم: یک پایگاه‌دانش متعلق به یک کاربر **و** یک پروژه‌ی
مشخص است (این برنامه هیچ مفهوم «اشتراک پروژه» ندارد)، بنابراین ``get_by_id`` هم
``user_id`` و هم ``project_id`` را بررسی می‌کند -- دقیقاً مثل الگوی «۴۰۴ نه ۴۰۳»ی
که در بقیه‌ی برنامه استفاده شده (تا وجود/عدم‌وجود پایگاه‌دانش کاربر دیگر لو نرود).

قرارداد نسخه‌بندی: هر بار که کاربر فایل‌ها را دوباره آپلود/اجرا می‌کند، یک ردیف
**جدید** با ``id`` (UUID) جدید ساخته می‌شود -- هرگز یک پایگاه‌دانش موجود بازنویسی
یا از نو استفاده نمی‌شود (نگاه کنید به ``create``).
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db.models import KBFile, KnowledgeBase, Project, new_kb_id, utcnow

READY = "ready"
INDEXING = "indexing"
FAILED = "failed"


# ---------------------------------------------------------------------------
# ساخت / خواندن
# ---------------------------------------------------------------------------
def create(
    session: Session,
    *,
    project_id: int,
    user_id: int,
    embedding_model: str,
    embedding_device: str,
    chroma_collection_name: str,
    chroma_persist_dir: str,
    status: str = INDEXING,
    kb_id: Optional[str] = None,
) -> KnowledgeBase:
    """یک ردیف پایگاه‌دانش **جدید** می‌سازد (هرگز چیزی را بازنویسی نمی‌کند).

    ``id`` پیش‌فرض به‌صورت خودکار یک UUID تازه است (``KnowledgeBase.id``
    پیش‌فرض ``new_kb_id`` را دارد)، بنابراین هر فراخوانی این تابع -- حتی برای
    همان ``(user_id, project_id)`` -- یک نسخه‌ی کاملاً مستقل و جدید تولید
    می‌کند. اگر فراخواننده از قبل به شناسه نیاز دارد (مثلاً برای ساختن مسیر
    دیسک/نام کالکشن Chroma پیش از درج ردیف)، می‌تواند آن را با ``kb_id``
    صراحتاً بدهد -- در این صورت همان مقدار (نه یک UUID تازه) استفاده می‌شود.
    """
    kb = KnowledgeBase(
        id=kb_id or new_kb_id(),
        project_id=project_id,
        user_id=user_id,
        embedding_model=embedding_model,
        embedding_device=embedding_device,
        chroma_collection_name=chroma_collection_name,
        chroma_persist_dir=chroma_persist_dir,
        status=status,
    )
    session.add(kb)
    session.commit()
    return kb


def get_by_id(
    session: Session, kb_id: str, *, user_id: int, project_id: int
) -> Optional[KnowledgeBase]:
    """پایگاه‌دانش فقط اگر متعلق به همین کاربر **و** همین پروژه باشد برگردانده می‌شود.

    مطابق الگوی رایج در این برنامه، عدم‌تطابق مالکیت مثل «پیدا نشد» رفتار
    می‌کند (نه یک استثنای مجزا)، تا لایه‌ی بالاتر بتواند بدون افشای اطلاعات
    اضافی یک ۴۰۴ برگرداند.
    """
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None or kb.user_id != user_id or kb.project_id != project_id:
        return None
    return kb


def list_by_project(session: Session, project_id: int) -> list[KnowledgeBase]:
    """همه‌ی پایگاه‌دانش‌های یک پروژه، جدیدترین اول."""
    stmt = (
        select(KnowledgeBase)
        .where(KnowledgeBase.project_id == project_id)
        .order_by(KnowledgeBase.created_at.desc(), KnowledgeBase.id.desc())
    )
    return list(session.scalars(stmt))


# ---------------------------------------------------------------------------
# به‌روزرسانی وضعیت
# ---------------------------------------------------------------------------
def update_status(
    session: Session,
    kb_id: str,
    *,
    status: str,
    error_message: Optional[str] = None,
) -> Optional[KnowledgeBase]:
    """تغییر وضعیت یک پایگاه‌دانش (indexing/ready/failed) بدون تغییر اشاره‌گر پروژه."""
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None:
        return None
    kb.status = status
    kb.error_message = error_message
    kb.updated_at = utcnow()
    session.commit()
    return kb


def mark_ready_and_update_project_pointer(
    session: Session, kb_id: str
) -> Optional[KnowledgeBase]:
    """یک پایگاه‌دانش را ``ready`` می‌کند **و** ``projects.latest_ready_kb_id`` را

    در همان تراکنش به‌روز می‌کند -- تا هیچ‌گاه پروژه به یک پایگاه‌دانشِ نیمه‌آماده
    یا وجود نداشته اشاره نکند (هر دو نوشته باهم commit می‌شوند).
    """
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None:
        return None

    kb.status = READY
    kb.error_message = None
    kb.updated_at = utcnow()

    project = session.get(Project, kb.project_id)
    if project is not None:
        project.latest_ready_kb_id = kb.id

    session.commit()
    return kb


# ---------------------------------------------------------------------------
# حذف
# ---------------------------------------------------------------------------
def delete_by_id(session: Session, kb_id: str) -> bool:
    """حذف ردیف پایگاه‌دانش (و ردیف‌های ``kb_files`` وابسته، از طریق cascade).

    اگر این پایگاه‌دانش، ``latest_ready_kb_id`` پروژه‌اش بود، آن اشاره‌گر هم
    پاک می‌شود (``ON DELETE SET NULL``/رفتار سطح ORM) تا هرگز به ردیف حذف‌شده
    اشاره نکند. مسئولیت پاک‌کردن فایل‌های روی دیسک با این تابع نیست --
    ``api/services/kb_storage.py::delete_kb_directory`` را جداگانه صدا بزنید.
    """
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None:
        return False

    project = session.get(Project, kb.project_id)
    if project is not None and project.latest_ready_kb_id == kb.id:
        project.latest_ready_kb_id = None
        session.flush()

    session.delete(kb)
    session.commit()
    return True


# ---------------------------------------------------------------------------
# فایل‌های هر پایگاه‌دانش (فقط برای تشخیص/ردیابی)
# ---------------------------------------------------------------------------
def add_file(
    session: Session,
    knowledge_base_id: str,
    *,
    file_key: str,
    original_filename: str,
    status: str = "pending",
    sheet_count: int = 0,
    chunk_count: int = 0,
    error: Optional[str] = None,
) -> KBFile:
    kb_file = KBFile(
        knowledge_base_id=knowledge_base_id,
        file_key=file_key,
        original_filename=original_filename,
        status=status,
        sheet_count=sheet_count,
        chunk_count=chunk_count,
        error=error,
    )
    session.add(kb_file)
    session.commit()
    return kb_file


def list_files(session: Session, knowledge_base_id: str) -> list[KBFile]:
    stmt = select(KBFile).where(KBFile.knowledge_base_id == knowledge_base_id)
    return list(session.scalars(stmt))


# ---------------------------------------------------------------------------
# سیاست نگهداری (retention) -- فقط پرس‌وجو در این فاز؛ اعمال آن در فاز بعد است
# ---------------------------------------------------------------------------
def list_stale_beyond_retention(
    session: Session, project_id: int, keep_count: int
) -> list[KnowledgeBase]:
    """پایگاه‌دانش‌های ``ready``/``failed`` یک پروژه، فراتر از ``keep_count`` مورد اخیر.

    خروجی از قدیمی‌ترین به جدیدترین مرتب شده است (ترتیب حذف طبیعی: ابتدا
    قدیمی‌ترین‌ها). پایگاه‌دانش‌های در حال ساخت (``indexing``) هرگز در این فهرست
    نمی‌آیند -- هیچ‌گاه نباید یک اجرای در حال انجام را «قدیمی» تلقی کرد.
    """
    if keep_count < 0:
        keep_count = 0

    stmt = (
        select(KnowledgeBase)
        .where(
            KnowledgeBase.project_id == project_id,
            KnowledgeBase.status.in_((READY, FAILED)),
        )
        .order_by(KnowledgeBase.created_at.desc(), KnowledgeBase.id.desc())
    )
    ordered_newest_first = list(session.scalars(stmt))
    stale_newest_first = ordered_newest_first[keep_count:]
    return list(reversed(stale_newest_first))
