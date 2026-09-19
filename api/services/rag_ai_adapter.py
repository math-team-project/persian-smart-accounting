"""تنها آداپتور مجاز بین این داشبورد و پکیج مستقل ``rag_chat_module``.

``rag_chat_module`` (در سطح ایمپورت ``llm_variable_resolver`` نامیده می‌شود)
یک پکیج پایتون کاملاً مستقل و از پیش پیاده‌سازی‌شده است که کنار این پروژه قرار
دارد. طبق طراحی، **هیچ فایلی از آن پکیج در این فاز تغییر نمی‌کند**؛ این ماژول
تنها نقطه‌ای است که در کل داشبورد اجازه دارد اشیای آن پکیج (``OpenAIClient``،
``SentenceTransformerEmbedder``، ``ChromaVectorStore``) را بسازد -- هر کارگاه
دیگری که در آینده به این‌ها نیاز داشته باشد باید از همین توابع استفاده کند، نه
اینکه دوباره متغیر محیطی/فایل YAML بخواند یا مستقیماً وارد پکیج شود.

نکته‌ی مهم درباره‌ی پیکربندی: ``rag_chat_module`` معمولاً اشیای خودش
(Voter/EnsembleExtractor/...) را از دو فایل ``providers.yaml`` و
``pipeline.yaml`` می‌سازد. این داشبورد عمداً به آن فایل‌ها دست نمی‌زند و
نمی‌نویسد -- در عوض همان اشیا (اینجا فقط ``OpenAIClient``) را مستقیماً در
پایتون، در لحظه‌ی فراخوانی، از زنجیره‌ی حل‌شده‌ی ``ai_settings.py`` همین پروژه
می‌سازد. این یعنی ``rag_chat_module`` کاملاً مستقل و قابل‌استفاده با CLI/اسکریپت‌های
خودش (و فایل‌های YAML خودش) باقی می‌ماند.

هشدار محدوده: این ماژول هنوز به هیچ کارگاهی وصل نشده است (نه چک‌لیست، نه
دستیار مالی) -- فقط زیرساخت آماده‌ی مصرف در فاز بعد است.

سه بخش مجزا:

۱) ساخت اشیای ``rag_chat_module`` از صفر (``build_llm_client``،
   ``build_embedder``، ``build_vector_store_factory``) -- برای *نوشتن* یک
   پایگاه‌دانش تازه.
۲) ``build_recording_vector_store_factory`` -- همان factory با یک پوشش نازک که
   متن هر chunk را کنار Chroma ذخیره می‌کند (نگاه کنید به
   ``api/services/kb_storage.py`` برای توضیح «چرا»).
۳) ``build_readonly_knowledge_base`` -- بازکردن یک پایگاه‌دانش **موجود** برای
   خواندن/جست‌وجو، بدون ایندکس‌کردن دوباره‌ی چیزی.
"""
from __future__ import annotations

import logging
import sys
from collections.abc import Callable

from sqlalchemy.orm import Session

from api.config import ROOT_DIR, get_settings
from api.services import ai_settings as ai_settings_module
from api.services import kb_storage

logger = logging.getLogger(__name__)

# اسلاگ کارگاه آینده‌ی «دستیار مالی» (چت‌بات RAG) در جدول ``workshop_settings``.
# با همین رشته، پنل تنظیمات AI هر پروژه می‌تواند بعداً برای این کارگاه هم یک فرم
# نشان دهد -- بدون هیچ حالت‌ویژه‌ای در ``ai_settings.py`` (که اصلاً به لیست
# اسلاگ‌های شناخته‌شده وابسته نیست؛ هر رشته‌ای را می‌پذیرد).
FINANCIAL_CHATBOT_WORKSHOP_SLUG = "financial_chatbot"

# پکیج ``rag_chat_module`` کنار این پروژه است، نه زیرمجموعه‌ی آن؛ نام ایمپورتش
# ``llm_variable_resolver`` است (پوشه‌ی ``rag_chat_module/llm_variable_resolver``).
# این پوشه هیچ ``__init__.py``ای در ریشه‌ی ``rag_chat_module`` ندارد -- دقیقاً
# مثل الگوی «namespace package» مستندشده در README برای ``extraction_script``:
# هر نقطه‌ی ورودی که به آن نیاز دارد، ریشه‌اش را یک‌بار به ``sys.path`` اضافه
# می‌کند.
_RAG_MODULE_ROOT = ROOT_DIR / "rag_chat_module"
if str(_RAG_MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(_RAG_MODULE_ROOT))


def _torch_cuda_available() -> bool:
    """تشخیص صریح CUDA پیش از ساخت embedder (به‌جای اعتماد به ``device=\"auto\"``).

    ``device=\"auto\"`` خودش هم CUDA را تشخیص می‌دهد، اما این تشخیص را از بیرون
    پنهان می‌کند. با تشخیص دستی، هم می‌توانیم دستگاه واقعی را در لاگ/پایگاه‌داده/
    ``manifest.json`` ثبت کنیم و هم یک رشته‌ی صریح (``\"cuda\"``/``\"cpu\"``) به
    ``SentenceTransformerEmbedder`` بدهیم.
    """
    try:
        import torch
    except ImportError:
        return False
    try:
        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 -- محیط‌های عجیب درایور/سخت‌افزار
        logger.warning("torch.cuda.is_available() raised; falling back to CPU", exc_info=True)
        return False


def resolve_embedding_device() -> str:
    """``\"cuda\"`` یا ``\"cpu\"`` -- هرچه واقعاً روی این ماشین در دسترس است."""
    if _torch_cuda_available():
        logger.info("CUDA is available; embedding model will run on GPU.")
        return "cuda"
    logger.info("CUDA is not available; embedding model will run on CPU.")
    return "cpu"


# ---------------------------------------------------------------------------
# LLM (OpenAI-compatible client)
# ---------------------------------------------------------------------------
def build_llm_client(
    session: Session,
    project_id: int,
    workshop_slug: str = FINANCIAL_CHATBOT_WORKSHOP_SLUG,
    *,
    client_cls: type | None = None,
):
    """می‌سازد یک llm_variable_resolver.llm.OpenAIClient آماده‌ی استفاده.

    تنظیمات (کلید/آدرس/مدل/دما/حداکثر طول خروجی) از همان زنجیره‌ی حل پیش‌فرض
    این پروژه (ai_settings.py::resolve_ai_settings) می‌آید -- نه از
    rag_chat_module/config/providers.yaml. این تنها جای مجاز برای ساخت
    OpenAIClient مخصوص این پکیج در کل داشبورد است.

    ``client_cls`` فقط برای تست است: به‌جای ایمپورت واقعی پکیج (که به شبکه/مدل
    نیاز ندارد اما در تست‌ها همچنان قرار نیست به آن وابسته باشیم) یک کلاس جعلی
    پاس داده می‌شود؛ در کد واقعی هرگز پاس داده نمی‌شود و از ایمپورت تنبل پیش‌فرض
    استفاده می‌شود.
    """
    if client_cls is None:
        from llm_variable_resolver.llm import OpenAIClient as client_cls

    settings = ai_settings_module.resolve_ai_settings(session, project_id, workshop_slug)
    return client_cls(
        model=settings.model,
        api_key=settings.api_key,
        base_url=settings.api_url,
        temperature=settings.temperature,
        max_tokens=settings.max_output_tokens,
    )


def build_llm_client_from_settings(settings, *, client_cls: type | None = None):
    """مثل ``build_llm_client`` اما از یک ``AISettings`` حل‌شده (بدون نشست پایگاه‌داده).

    برای «تست اتصال» فرم تنظیمات لازم است: آن مسیر یک ``AISettings`` سوارشده با
    مقادیر ذخیره‌نشده‌ی فرم را در دست دارد و نباید به‌خاطر یک تست، resolver را
    دوباره صدا بزند (که مقادیر ذخیره‌شده را برمی‌گرداند). این تابع همچنان تنها
    جای مجاز ساخت ``OpenAIClient`` در داشبورد است.
    """
    if client_cls is None:
        from llm_variable_resolver.llm import OpenAIClient as client_cls

    return client_cls(
        model=settings.model,
        api_key=settings.api_key,
        base_url=settings.api_url,
        temperature=settings.temperature,
        max_tokens=settings.max_output_tokens,
    )


# ---------------------------------------------------------------------------
# Embedder
# ---------------------------------------------------------------------------
def build_embedder(
    *,
    model_name: str | None = None,
    device: str | None = None,
    embedder_cls: type | None = None,
):
    """می‌سازد یک SentenceTransformerEmbedder با مدل/کش/دستگاه مشخص.

    ``model_name`` پیش‌فرض از PSA_EMBEDDING_MODEL می‌آید (مدل‌ها روی دیسک کش
    می‌شوند و معمولاً بین اجراها ثابت می‌مانند)، و ``device`` -- اگر پاس داده
    نشود -- با تشخیص واقعی CUDA در همین فرآیند تعیین می‌شود (نه با دستور
    ``device="auto"``، تا مقدار واقعی برای لاگ/ثبت در پایگاه‌داده در دسترس
    باشد).

    ``embedder_cls`` فقط برای تست است (تا بارگیری/شبکه واقعی در تست‌ها لازم نباشد).

    خروجی: ``(embedder, resolved_device)`` -- ``resolved_device`` همان چیزی
    است که باید در ستون ``knowledge_bases.embedding_device`` و در
    ``manifest.json`` ذخیره شود.
    """
    if embedder_cls is None:
        from llm_variable_resolver.retrieval import SentenceTransformerEmbedder as embedder_cls

    settings = get_settings()
    resolved_model = model_name or settings.embedding_model
    resolved_device = device or resolve_embedding_device()

    embedder = embedder_cls(
        model_name=resolved_model,
        cache_dir=str(kb_storage.models_cache_dir()),
        device=resolved_device,
    )
    # اگر خودِ embedder دستگاه دیگری را واقعاً انتخاب کرده باشد (مثلاً به‌خاطر
    # نبود سخت‌افزار مناسب) همان مقدار واقعی را برمی‌گردانیم -- نه آنچه درخواست
    # شده بود.
    actual_device = getattr(embedder, "resolved_device", None) or resolved_device
    if actual_device != resolved_device:
        logger.info(
            "embedder fell back from requested device=%s to actual device=%s",
            resolved_device,
            actual_device,
        )
    return embedder, actual_device


# ---------------------------------------------------------------------------
# Vector store factory
# ---------------------------------------------------------------------------
def chroma_collection_name_for(kb_id: str) -> str:
    """نام کالکشن Chroma را به‌صورت قطعی از ``kb_id`` می‌سازد (بدون برخورد بین KBها)."""
    return f"kb-{kb_id}"


def build_vector_store_factory(
    user_id: int, project_id: int, kb_id: str, *, store_cls: type | None = None
) -> Callable[[], object]:
    """یک factory بدون آرگومان برمی‌گرداند که یک ChromaVectorStore تازه می‌سازد.

    ``persist_directory`` از ``kb_storage.path_for`` می‌آید (تنها منبع حقیقت
    درباره‌ی محل فایل‌های دیسک هر پایگاه‌دانش) و نام کالکشن به‌صورت قطعی از
    ``kb_id`` مشتق می‌شود، بنابراین هر ``kb_id`` جدید همیشه یک ایندکس مستقل و
    تازه دارد.

    ``store_cls`` فقط برای تست است.
    """

    def _factory():
        cls = store_cls
        if cls is None:
            from llm_variable_resolver.retrieval import ChromaVectorStore as cls

        return cls(
            collection_name=chroma_collection_name_for(kb_id),
            persist_directory=str(kb_storage.path_for(user_id, project_id, kb_id)),
        )

    return _factory


# ---------------------------------------------------------------------------
# factory «ضبط‌کننده» -- برای زمانی که یک پایگاه‌دانش نازه ساخته می‌شود
# ---------------------------------------------------------------------------
class _ChunkRecordingVectorStore:
    """پوشش نازک دور یک ``VectorStore`` که متن هر chunk را هم روی دیسک می‌نویسد.

    خودش هیچ جست‌وجویی انجام نمی‌دهد؛ هر دو متد را دقیقاً به استور درونی
    واگذار می‌کند و فقط کنار ``add`` یک نسخه‌ی متنی هم در ``chunks.jsonl`` همان
    پایگاه‌دانش می‌گذارد (نگاه کنید به ``api/services/kb_storage.py`` برای توضیح
    اینکه چرا بدون این فایل، یک فرایند تازه هیچ چیزی برای جست‌وجو ندارد).

    ترتیب مهم است: ابتدا ``add`` درونی (تا فقط chunk هایی که واقعاً وارد ایندکس
    شده‌اند ثبت شوند)، بعد نوشتن متن.
    """

    def __init__(
        self,
        inner: object,
        *,
        user_id: int,
        project_id: int,
        kb_id: str,
        state: dict[str, set[str]],
    ) -> None:
        self._inner = inner
        self._user_id = user_id
        self._project_id = project_id
        self._kb_id = kb_id
        self._state = state

    def add(self, chunk, vector) -> None:  # noqa: ANN001 -- امضای VectorStore
        self._inner.add(chunk, vector)  # type: ignore[attr-defined]
        kb_storage.append_chunks(
            self._user_id, self._project_id, self._kb_id, [chunk], state=self._state
        )

    def search(self, query_vector, top_k: int):  # noqa: ANN001 -- امضای VectorStore
        return self._inner.search(query_vector, top_k)  # type: ignore[attr-defined]


def build_recording_vector_store_factory(
    user_id: int, project_id: int, kb_id: str, *, store_cls: type | None = None
):
    """factory‌ای مثل ``build_vector_store_factory`` که متن chunk ها را هم ذخیره می‌کند.

    فقط برای مسیر **ساخت** پایگاه‌دانش (ایندکس‌سازی) استفاده می‌شود؛ مسیر خواندن
    از ``build_vector_store_factory`` ساده استفاده می‌کند. هر فراخوانی factory یک
    استور تازه می‌سازد، اما همه‌ی آن‌ها متن را به همان ``chunks.jsonl`` اضافه
    می‌کنند و شناسه‌های نوشته‌شده بین‌شان مشترک است (تا یک فایل دوبار ایندکس‌شده
    رکورد تکراری تولید نکند).
    """
    state: dict[str, set[str]] = {}
    base_factory = build_vector_store_factory(user_id, project_id, kb_id, store_cls=store_cls)

    def _factory():
        return _ChunkRecordingVectorStore(
            base_factory(),
            user_id=user_id,
            project_id=project_id,
            kb_id=kb_id,
            state=state,
        )

    return _factory


# ---------------------------------------------------------------------------
# پایگاه‌دانش فقط-خواندنی -- برای جست‌وجو/چت روی یک ایندکس موجود
# ---------------------------------------------------------------------------
_FITTED_FLAG = "_fitted"
_STORE_ATTR = "_store"
_CHUNKS_BY_ID_ATTR = "_chunks_by_id"
_STORE_CACHE_ATTR = "_chunk_cache"


def build_readonly_knowledge_base(
    user_id: int,
    project_id: int,
    kb_id: str,
    *,
    embedder=None,
    embedder_cls: type | None = None,
    store_cls: type | None = None,
):
    """یک ``KnowledgeBase`` آماده‌ی جست‌وجو روی یک پایگاه‌دانش **موجود** می‌سازد.

    چرا این تابع لازم است: سازنده‌ی ``KnowledgeBase`` برای *ساختن* ایندکس طراحی
    شده -- تا زمانی که ``index_files`` صدا زده نشود، ``is_ready()`` مقدار
    ``False`` برمی‌گرداند و ``chat_ask`` بلافاصله «پایگاه دانش آماده نیست»
    می‌دهد. برای یک فرایند تازه (درخواست چت‌بات) هیچ چیزی برای ایندکس‌کردن مجدد
    وجود ندارد: فایل‌های ورودی کاربر پس از هر اجرا حذف می‌شوند. بنابراین حالت
    «آماده» و کش درون‌حافظه‌ای استور را از روی ``chunks.jsonl`` (که در زمان
    ایندکس‌سازی نوشته شده) بازسازی می‌کنیم و بقیه‌ی مسیر دقیقاً همان کد
    ``rag_chat_module`` می‌ماند (``search`` → ``_answer_with_llm`` با روتر).

    خروجی ``None`` یعنی این پایگاه‌دانش هیچ متن قابل‌جست‌وجویی ندارد (مثلاً
    قبل از این تغییر ساخته شده) -- تصمیم با فراخواننده است که پیام فارسی
    مناسب را نشان دهد، نه اینکه یک خطای مبهم پرتاب شود.

    تنظیمات و اشیای مستقل (embedder/vector store) از همان دو تابع بالای همین
    فایل می‌آیند؛ هیچ‌جای دیگری در داشبورد مجاز به ساخت آن‌ها نیست.
    """
    records = kb_storage.read_chunks(user_id, project_id, kb_id)
    if not records:
        logger.warning(
            "read-only knowledge base requested but no chunk text is stored (kb=%s)", kb_id
        )
        return None

    if embedder is None:
        embedder, _device = build_embedder(embedder_cls=embedder_cls)
    store_factory = build_vector_store_factory(user_id, project_id, kb_id, store_cls=store_cls)

    from llm_variable_resolver.knowledge_base import KnowledgeBase

    knowledge_base = KnowledgeBase(embedder=embedder, vector_store_factory=store_factory)
    store = store_factory()

    # بازسازی کش درون‌حافظه‌ای استور: ``ChromaVectorStore.search`` هر رکورد را از
    # ``_chunk_cache`` می‌خواند (چون متن را در Chroma ذخیره نمی‌کند) -- بدون این
    # کار، جست‌وجو همیشه خالی برمی‌گردد. اگر استور واقعاً چنین صفتی نداشته باشد
    # (مثلاً یک کلاس تست) بی‌سروصدا رد می‌شود.
    cache = getattr(store, _STORE_CACHE_ATTR, None)
    if isinstance(cache, dict):
        cache.update({chunk.chunk_id: chunk for chunk in records})

    setattr(knowledge_base, _FITTED_FLAG, True)
    setattr(knowledge_base, _STORE_ATTR, store)
    setattr(knowledge_base, _CHUNKS_BY_ID_ATTR, {chunk.chunk_id: chunk for chunk in records})

    logger.info(
        "read-only knowledge base ready (kb=%s, chunks=%d)", kb_id, len(records)
    )
    return knowledge_base
