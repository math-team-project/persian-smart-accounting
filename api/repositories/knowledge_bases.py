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
# حذف در مقیاس «کل پروژه» -- فقط از مسیر حذف پروژه صدا زده می‌شود
# ---------------------------------------------------------------------------
def delete_all_knowledge_bases_for_project(
    session: Session, project_id: int, user_id: int
) -> dict[str, int]:
    """**همه‌ی** پایگاه‌دانش‌های یک پروژه (و یک کاربر) را با ``kb_files``شان پاک می‌کند.

    خروجی: ``{"knowledge_bases": n, "kb_files": m}`` (شمار واقعی ردیف‌های حذف‌شده).

    قرارداد نام‌گذاری: برخلاف ``delete_by_id`` (یک پایگاه‌دانش مشخص)، این تابع
    کل پایگاه‌دانش‌های یک پروژه را هدف می‌گیرد -- و نامش همین را می‌گوید. **تنها
    فراخواننده‌ی مجاز ``api/services/project_service.py`` است** و هیچ endpoint ای
    آن را در معرض HTTP نمی‌گذارد.

    دو نکته‌ی ایمنی:

    ۱) **اشاره‌گر پروژه پیش از حذف ردیف‌ها پاک می‌شود.** ``projects.latest_ready_kb_id``
       یک کلید خارجی به ``knowledge_bases.id`` است (``ON DELETE SET NULL``). پاک‌کردن
       صریح آن پیش از حذف ردیف‌ها یعنی هیچ ردیفی برای یک لحظه هم به یک پایگاه‌دانشِ
       روبه‌حذف اشاره نمی‌کند و حتی اگر روزی رفتار ``ON DELETE`` عوض شود، این تابع
       همچنان بدون نقض قید کار می‌کند.

    ۲) **``kb_files`` صریحاً و پیش از ردیف‌های والد حذف می‌شود** -- دقیقاً همان
       سیاست ``projects_repo.delete``: با اینکه ``ON DELETE CASCADE`` در سطح
       پایگاه‌داده وجود دارد، حذف صریح تضمین می‌کند هیچ ردیف یتیمی باقی نماند،
       حتی اگر PRAGMA کلیدهای خارجی روزی خاموش شود.

    مسئولیت پاک‌کردن پوشه‌های روی دیسک با این تابع نیست: ابتدا
    ``api/services/kb_storage.py::delete_vector_store_directories_for_project`` را
    صدا بزنید (ترتیب صحیح در ``project_service.delete_project`` آمده است).
    """
    project = session.get(Project, project_id)
    if project is None or project.user_id != user_id:
        # پروژه پیدا نشد یا متعلق به این کاربر نیست -- هیچ چیزی حذف نمی‌شود
        # (همان الگوی «۴۰۴ نه ۴۰۳»ی بقیه‌ی این ماژول).
        return {"knowledge_bases": 0, "kb_files": 0}

    kb_ids = list(
        session.scalars(
            select(KnowledgeBase.id).where(
                KnowledgeBase.project_id == project_id,
                KnowledgeBase.user_id == user_id,
            )
        )
    )
    if not kb_ids:
        return {"knowledge_bases": 0, "kb_files": 0}

    if project.latest_ready_kb_id in kb_ids:
        project.latest_ready_kb_id = None
        session.flush()

    file_rows = (
        session.query(KBFile)
        .filter(KBFile.knowledge_base_id.in_(kb_ids))
        .delete(synchronize_session=False)
    )
    kb_rows = (
        session.query(KnowledgeBase)
        .filter(KnowledgeBase.project_id == project_id, KnowledgeBase.user_id == user_id)
        .delete(synchronize_session=False)
    )
    session.commit()
    return {"knowledge_bases": int(kb_rows), "kb_files": int(file_rows)}


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

    **پایگاه‌دانشی که همین حالا ``projects.latest_ready_kb_id`` به آن اشاره
    می‌کند هرگز در خروجی نیست** -- و این استثنا در *همان دستور SQL* اعمال
    می‌شود، نه با مقایسه‌ی یک مقدار که قبلاً (در پایتون) خوانده شده است.
    دلیلش یک حالت مسابقه‌ی واقعی است: ترتیب این فهرست بر اساس ``created_at``
    است، در حالی که اشاره‌گر پروژه بر اساس «چه زمانی آماده شد» جلو می‌رود؛ پس
    دو اجرای هم‌زمان می‌توانند کاری کنند که اشاره‌گر به پایگاه‌دانشی اشاره کند
    که در این ترتیب جدیدترین نیست. اگر تصمیم «کدام‌ها زائدند» و «کدام یکی
    فعلی است» دو خواندن جدا باشند، بین‌شان یک اجرای دیگر می‌تواند اشاره‌گر را
    جابه‌جا کند و همان پایگاه‌دانشِ در حال استفاده حذف شود. با آوردن زیرپرس‌وجوی
    اشاره‌گر داخل همین ``SELECT``، این فاصله‌ی «بررسی تا استفاده» بسته می‌شود.

    ``is_distinct_from`` عمداً به‌جای ``!=`` استفاده شده تا رفتار NULL درست باشد:
    وقتی اشاره‌گر پروژه خالی است، ``id != NULL`` در SQL مقدار NULL (نه «درست»)
    می‌دهد و همه‌ی ردیف‌ها بی‌دلیل از فهرست می‌افتادند. ``IS NOT`` این حالت را
    درست مدیریت می‌کند (هر ``id`` غیرتهی «متفاوت از NULL» است).
    """
    if keep_count < 0:
        keep_count = 0

    # ``keep_count`` ردیفِ جدیدترین همیشه نگه داشته می‌شوند -- و این «نگه‌داشتن»
    # با یک زیرپرس‌وجوی ``LIMIT`` بیان می‌شود، نه با بریدن یک لیست در پایتون.
    # دقت کنید که اشاره‌گر فعلی از این مجموعه‌ی نگه‌داشته‌شده *کم نمی‌کند*:
    # اگر ابتدا اشاره‌گر را حذف و بعد ``keep_count`` را اعمال می‌کردیم، با
    # ``keep_count=1`` عملاً هیچ پایگاه‌دانشی زائد تشخیص داده نمی‌شد و سیاست
    # نگهداری هرگز چیزی پاک نمی‌کرد.
    newest_kept = (
        select(KnowledgeBase.id)
        .where(
            KnowledgeBase.project_id == project_id,
            KnowledgeBase.status.in_((READY, FAILED)),
        )
        .order_by(KnowledgeBase.created_at.desc(), KnowledgeBase.id.desc())
        .limit(keep_count)
    )

    current_latest = (
        select(Project.latest_ready_kb_id)
        .where(Project.id == project_id)
        .scalar_subquery()
    )

    # ترتیب خروجی از قدیمی‌ترین به جدیدترین است -- همان ترتیب طبیعی حذف.
    stmt = (
        select(KnowledgeBase)
        .where(
            KnowledgeBase.project_id == project_id,
            KnowledgeBase.status.in_((READY, FAILED)),
            KnowledgeBase.id.not_in(newest_kept),
            KnowledgeBase.id.is_distinct_from(current_latest),
        )
        .order_by(KnowledgeBase.created_at.asc(), KnowledgeBase.id.asc())
    )
    return list(session.scalars(stmt))
