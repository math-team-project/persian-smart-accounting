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
