"""تنظیمات هوش مصنوعی هر کارگاه در هر پروژه -- منبع واحد «حل پیش‌فرض‌ها».

هر پروژه می‌تواند برای هر کارگاه خودش کلید API، آدرس سرویس، نام مدل و چند
پارامتر دیگر را جداگانه تعیین کند. این ماژول تنها جایی است که آن تنظیمات خوانده،
اعتبارسنجی، رمزنگاری و به یک ``AISettings`` **کاملاً پرشده** تبدیل می‌شوند؛ هیچ
کارگاهی منطق خودش را برای این کار ندارد.

سه مسئولیت:

۱) **حل پیش‌فرض‌ها** (``resolve_ai_settings``): مقدار ذخیره‌شده‌ی کاربر اولویت
   دارد و هر فیلدی که تنظیم نشده باشد بی‌سروصدا به پیش‌فرض سامانه برمی‌گردد.
   هیچ‌گاه به‌خاطر خالی‌بودن یک فیلد خطا داده نمی‌شود؛ خروجی همیشه پرشده است.

۲) **رمزنگاری کلید** (``encrypt_secret``/``decrypt_secret``): کلید API هرگز به
   متن خام در پایگاه‌داده نوشته نمی‌شود. با Fernet (پکیج ``cryptography``) و کلید
   مصوب ``PSA_AI_SETTINGS_ENCRYPTION_KEY`` رمز می‌شود و فقط در لحظه‌ی استفاده در
   فرایند سرور رمزگشایی می‌گردد. اگر رمزگشایی شکست بخورد (مثلاً کلید عوض شده
   باشد) مقدار «تنظیم نشده» در نظر گرفته می‌شود و به پیش‌فرض برمی‌گردد -- باز هم
   بدون خطا، اما با ثبت هشدار در لاگ.

۳) **تبدیل به تنظیمات هر کارگاه**: لایه‌ی ``api`` نوع خنثای ``AISettings`` را
   می‌سازد و کارگاه‌ها همان را به شکل موردنیاز خودشان می‌گیرند (نمونه:
   ``to_budget_llm_settings``). بنابراین ``budget_analysis`` هیچ‌گاه با جدول
   ``workshop_settings`` یا با رمزنگاری درگیر نمی‌شود.

پیش‌فرض‌های سامانه از همان متغیرهای محیطی مستندشده‌ی پروژه می‌آیند
(``AUDIT_REPORT_LLM_API_KEY`` ← ``API_KEY_OPENROUTER`` ← ``B_AI_API_KEY``،
``AUDIT_REPORT_LLM_BASE_URL`` و ``AUDIT_REPORT_LLM_MODEL``) و از طریق
``BudgetLLMSettings`` خوانده می‌شوند تا این مقادیر در دو جای متفاوت تکرار نشوند.
"""
from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass, replace
from typing import Any, Mapping, Optional

from sqlalchemy.orm import Session

from api.config import get_settings
from api.repositories import workshop_runs as runs_repo

logger = logging.getLogger(__name__)

__all__ = [
    "AISettings",
    "AISettingsError",
    "MASKED_KEY_PLACEHOLDER_FA",
    "decrypt_secret",
    "default_ai_settings",
    "encrypt_secret",
    "overlay",
    "panel_for",
    "resolve_ai_settings",
    "save_ai_settings",
    "to_budget_llm_settings",
    "validate_overrides",
]

# متن راهنمای فیلد کلید وقتی از قبل مقداری ذخیره شده است. کلید ذخیره‌شده هرگز به
# فرم برنمی‌گردد -- نه متن خام و نه مقدار رمزشده؛ فقط این نشانگر.
MASKED_KEY_PLACEHOLDER_FA = "•••• تنظیم شده — برای تغییر، مقدار جدید وارد کنید"

# نام کلیدهای اضافی که در ستون ``extra_settings`` نگه داشته می‌شوند. این دو مورد
# بیش از بقیه بر کیفیت و هزینه‌ی گزارش اثر می‌گذارند (بقیه‌ی پارامترهای فنی مثل
# timeout/retry عمداً در فرم نیستند تا فرم با گزینه‌های بی‌اثر شلوغ نشود).
EXTRA_TEMPERATURE = "temperature"
EXTRA_MAX_OUTPUT_TOKENS = "max_output_tokens"
EXTRA_KEYS = (EXTRA_TEMPERATURE, EXTRA_MAX_OUTPUT_TOKENS)

TEMPERATURE_MIN = 0.0
TEMPERATURE_MAX = 2.0
MAX_OUTPUT_TOKENS_MIN = 1
MAX_OUTPUT_TOKENS_MAX = 200_000

# پیشوند نسخه‌دار مقادیر رمزشده: یک رشته‌ی ذخیره‌شده بدون این پیشوند هرگز
# رمزگشایی نمی‌شود (اگر مقداری از قبل به‌صورت متن خام در پایگاه‌داده باشد،
# «تنظیم نشده» تلقی می‌شود). این پیشوند امکان تغییر الگوریتم در آینده را هم
# بدون حدس‌زدن درباره‌ی قالب مقادیر قدیمی می‌دهد.
_CIPHER_PREFIX = "fernet:v1:"


class AISettingsError(Exception):
    """خطای اعتبارسنجی تنظیمات؛ پیام آن فارسی و قابل‌نمایش به کاربر است."""


# ---------------------------------------------------------------------------
# مدل مقدار نهایی
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AISettings:
    """تنظیمات کامل و آماده‌ی استفاده برای یک کارگاه در یک پروژه.

    همه‌ی فیلدها مقدار دارند (``resolve_ai_settings`` هیچ‌گاه فیلد ``None``
    برنمی‌گرداند) به‌جز ``api_key`` که اگر هیچ کلیدی -- نه در تنظیمات پروژه و نه
    در محیط -- وجود نداشته باشد ``None`` می‌ماند؛ در آن حالت خود کارگاه پیام
    راهنمای فارسی مناسب را به کاربر می‌دهد.
    """

    api_key: Optional[str]
    api_url: str
    model: str
    temperature: float
    max_output_tokens: int
    request_timeout: float
    max_retries: int
    retry_backoff_seconds: float

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    @property
    def masked_api_key(self) -> str:
        """فقط برای لاگ -- هرگز کامل نمایش داده نمی‌شود."""
        key = self.api_key or ""
        return f"{key[:5]}…" if key else "(تنظیم نشده)"


# ---------------------------------------------------------------------------
# پیش‌فرض‌های سامانه
# ---------------------------------------------------------------------------
def default_ai_settings() -> AISettings:
    """پیش‌فرض‌های سامانه (از ``.env``/محیط) به‌صورت یک ``AISettings`` کامل.

    ایمپورت ``budget_analysis`` عمداً داخل تابع است: این ماژول در زمان ایمپورت
    پکیج کارگاه‌ها لازم نمی‌شود، و همان الگوی «ایمپورت تنبل» که خود
    ``budget_analysis.llm`` برای ``audit_report_generator`` به کار می‌برد.
    """
    from budget_analysis.llm import BudgetLLMSettings

    base = BudgetLLMSettings.from_env().resolved()
    return AISettings(
        api_key=base.api_key,
        api_url=base.base_url,
        model=base.model,
        temperature=base.temperature,
        max_output_tokens=base.max_output_tokens,
        request_timeout=base.request_timeout,
        max_retries=base.max_retries,
        retry_backoff_seconds=base.retry_backoff_seconds,
    )


# ---------------------------------------------------------------------------
# رمزنگاری کلید API
# ---------------------------------------------------------------------------
def _load_fernet():
    """نمونه‌ی Fernet یا ``None`` اگر پکیج رمزنگاری در دسترس نباشد.

    کلید از ``PSA_AI_SETTINGS_ENCRYPTION_KEY`` خوانده می‌شود. اگر تنظیم نشده یا
    نامعتبر باشد، یک کلید پایدار از ``PSA_SECRET_KEY`` مشتق می‌شود: در هر حالت
    هیچ‌وقت متن خام ذخیره نمی‌شود، فقط کلیدها بین اجراهای مختلف قابل‌رمزگشایی
    می‌مانند. نبودن پکیج ``cryptography`` هم برنامه را از کار نمی‌اندازد؛ فقط
    ذخیره‌ی کلید غیرفعال و کلیدِ ذخیره‌شده نادیده گرفته می‌شود.
    """
    try:
        from cryptography.fernet import Fernet
    except ImportError:  # pragma: no cover - وابستگی نصبی است، فقط حالت تنزل‌یافته
        logger.warning(
            "پکیج cryptography نصب نیست؛ ذخیره‌ی رمزشده‌ی کلید API ممکن نیست "
            "و کلیدهای ذخیره‌شده نادیده گرفته می‌شوند."
        )
        return None

    settings = get_settings()
    explicit = (settings.ai_settings_encryption_key or "").strip()
    if explicit:
        try:
            return Fernet(explicit.encode("ascii"))
        except (ValueError, TypeError):
            logger.warning(
                "PSA_AI_SETTINGS_ENCRYPTION_KEY یک کلید معتبر Fernet نیست؛ "
                "به‌جای آن از کلید مشتق‌شده از PSA_SECRET_KEY استفاده می‌شود."
            )

    derived = base64.urlsafe_b64encode(
        hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    )
    return Fernet(derived)


def encrypt_secret(plaintext: str) -> str:
    """رمزکردن یک راز برای ذخیره در پایگاه‌داده.

    Raises:
        AISettingsError: اگر رمزنگاری در دسترس نباشد (هیچ‌وقت متن خام ذخیره نمی‌شود).
    """
    fernet = _load_fernet()
    if fernet is None:
        raise AISettingsError(
            "ذخیره‌ی امن کلید API ممکن نیست: پکیج «cryptography» روی سرور نصب نیست. "
            "برای فعال‌شدن این بخش، وابستگی‌های پروژه را نصب کنید."
        )
    token = fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
    return f"{_CIPHER_PREFIX}{token}"


def decrypt_secret(stored: Optional[str]) -> Optional[str]:
    """رمزگشایی مقدار ذخیره‌شده؛ ``None`` یعنی «قابل‌استفاده نیست، پیش‌فرض را بگیر»."""
    if not stored:
        return None
    if not stored.startswith(_CIPHER_PREFIX):
        logger.warning(
            "یک مقدار کلید API بدون قالب رمزشده در پایگاه‌داده پیدا شد؛ نادیده گرفته "
            "می‌شود. کلید را از فرم تنظیمات دوباره وارد کنید."
        )
        return None

    fernet = _load_fernet()
    if fernet is None:
        return None
    try:
        return fernet.decrypt(stored[len(_CIPHER_PREFIX) :].encode("ascii")).decode("utf-8")
    except Exception:  # noqa: BLE001 -- کلید عوض شده یا مقدار خراب است
        logger.warning(
            "رمزگشایی کلید API ذخیره‌شده ناموفق بود (احتمالاً کلید رمزنگاری تغییر "
            "کرده است)؛ برای این اجرا از پیش‌فرض سامانه استفاده می‌شود. برای رفع "
            "دائمی، کلید را از فرم تنظیمات دوباره وارد کنید.",
            exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# کمک‌تابع‌های تبدیل مقدار
# ---------------------------------------------------------------------------
def _extras_of(row: Any) -> Mapping[str, Any]:
    raw = getattr(row, "extra_settings", None)
    return raw if isinstance(raw, Mapping) else {}


def _coerce_float(value: Any, fallback: float) -> float:
    if value is None or isinstance(value, bool):
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _coerce_int(value: Any, fallback: int) -> int:
    if value is None or isinstance(value, bool):
        return fallback
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def _clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# ---------------------------------------------------------------------------
# حل پیش‌فرض‌ها
# ---------------------------------------------------------------------------
def resolve_ai_settings(session: Session, project_id: int, workshop_slug: str) -> AISettings:
    """تنظیمات مؤثر یک کارگاه در یک پروژه -- همیشه کامل، هرگز نیمه‌خالی.

    ترتیب: مقدار ذخیره‌شده در ``workshop_settings`` (اگر پر باشد) ← پیش‌فرض
    سامانه. نبودن ردیف، ``None`` بودن ستون‌ها یا حتی رمزگشایی‌نشدن کلید همه به
    «از پیش‌فرض استفاده کن» منجر می‌شوند، نه به خطا.

    ``session`` لازم است چون این resolver در همان نشست درخواست اجرا می‌شود و
    خروجی‌اش (یک ``AISettings`` خنثا) به ترد پس‌زمینه پاس داده می‌شود؛ هیچ ترد
    پس‌زمینه‌ای خودش به پایگاه‌داده دست نمی‌زند.
    """
    defaults = default_ai_settings()
    row = runs_repo.get_setting(session, project_id, workshop_slug)
    if row is None:
        return defaults

    extras = _extras_of(row)
    return AISettings(
        api_key=decrypt_secret(row.api_key) or defaults.api_key,
        api_url=_clean_text(row.api_url) or defaults.api_url,
        model=_clean_text(row.model) or defaults.model,
        temperature=_coerce_float(extras.get(EXTRA_TEMPERATURE), defaults.temperature),
        max_output_tokens=_coerce_int(
            extras.get(EXTRA_MAX_OUTPUT_TOKENS), defaults.max_output_tokens
        ),
        request_timeout=defaults.request_timeout,
        max_retries=defaults.max_retries,
        retry_backoff_seconds=defaults.retry_backoff_seconds,
    )


# ---------------------------------------------------------------------------
# ذخیره
# ---------------------------------------------------------------------------
def validate_overrides(
    *, temperature: Optional[float] = None, max_output_tokens: Optional[int] = None
) -> None:
    """اعتبارسنجی مقادیر عددی فرم تنظیمات -- مشترک بین «ذخیره» و «تست اتصال».

    هر دو مسیر باید ورودی نامعتبر را *یکسان* رد کنند: پیش‌تر «تست اتصال» این
    مقادیر را اعتبارسنجی نمی‌کرد، بنابراین دمای بیرون از بازه در تست بی‌سروصدا
    به مدل فرستاده می‌شد و کاربر فقط هنگام ذخیره با خطا روبه‌رو می‌شد.

    Raises:
        AISettingsError: با پیام فارسی قابل‌نمایش به کاربر.
    """
    if temperature is not None and not TEMPERATURE_MIN <= temperature <= TEMPERATURE_MAX:
        raise AISettingsError(
            f"مقدار دما باید عددی بین {TEMPERATURE_MIN:g} و {TEMPERATURE_MAX:g} باشد."
        )
    if (
        max_output_tokens is not None
        and not MAX_OUTPUT_TOKENS_MIN <= max_output_tokens <= MAX_OUTPUT_TOKENS_MAX
    ):
        raise AISettingsError(
            "حداکثر طول خروجی باید عددی بین "
            f"{MAX_OUTPUT_TOKENS_MIN} و {MAX_OUTPUT_TOKENS_MAX:,} باشد."
        )


def save_ai_settings(
    session: Session,
    project_id: int,
    workshop_slug: str,
    *,
    api_key: Optional[str] = None,
    clear_api_key: bool = False,
    api_url: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_output_tokens: Optional[int] = None,
) -> AISettings:
    """ذخیره‌ی تنظیمات یک کارگاه و برگرداندن مقدار مؤثر پس از ذخیره.

    فیلدهای متنی خالی (``""``/``None``) یعنی «پاک کن تا پیش‌فرض به کار بیفتد».
    ``api_key`` استثناست: چون مقدار ذخیره‌شده هرگز به فرم برنمی‌گردد، خالی‌بودن
    آن یعنی «همان کلید قبلی را نگه دار» و پاک‌کردن کلید فقط با
    ``clear_api_key=True`` انجام می‌شود.

    Raises:
        AISettingsError: مقدار نامعتبر (مثلاً دمای بیرون از بازه) با پیام فارسی.
    """
    api_key = _clean_text(api_key)
    if api_key is None and clear_api_key:
        key_update: Any = None  # پاک‌کردن کلید ذخیره‌شده
    elif api_key is None:
        key_update = runs_repo.UNSET  # دست‌نزدن به کلید فعلی
    else:
        key_update = encrypt_secret(api_key)

    validate_overrides(temperature=temperature, max_output_tokens=max_output_tokens)

    extras: dict[str, Any] = {}
    if temperature is not None:
        extras[EXTRA_TEMPERATURE] = temperature
    if max_output_tokens is not None:
        extras[EXTRA_MAX_OUTPUT_TOKENS] = max_output_tokens

    runs_repo.upsert_setting(
        session,
        project_id,
        workshop_slug,
        api_key=key_update,
        api_url=_clean_text(api_url),
        model=_clean_text(model),
        extra_settings=extras or None,
    )
    logger.info(
        "AI settings saved (project=%s, workshop=%s, key_set=%s, temperature=%s, max_tokens=%s)",
        project_id,
        workshop_slug,
        key_update is not runs_repo.UNSET and key_update is not None,
        temperature,
        max_output_tokens,
    )
    return resolve_ai_settings(session, project_id, workshop_slug)


# ---------------------------------------------------------------------------
# نمایش در فرم تنظیمات (هرگز شامل خود کلید نیست)
# ---------------------------------------------------------------------------
def panel_for(session: Session, project_id: int, workshop: Any) -> dict[str, Any]:
    """داده‌ی فرم تنظیمات یک کارگاه -- بدون هیچ رازی.

    تنها چیزی که درباره‌ی کلید به فرم می‌رود ``api_key_set`` است؛ خود کلید (خام
    یا رمزشده) هرگز به HTML نمی‌رسد. بقیه‌ی فیلدها مقدار ذخیره‌شده‌ی کاربر را
    نشان می‌دهند تا قابل ویرایش باشند.
    """
    row = runs_repo.get_setting(session, project_id, workshop.slug)
    extras = _extras_of(row)
    return {
        "workshop": workshop,
        "slug": workshop.slug,
        "api_key_set": bool(getattr(row, "api_key", None)),
        "api_url": _clean_text(getattr(row, "api_url", None)) or "",
        "model": _clean_text(getattr(row, "model", None)) or "",
        "temperature": extras.get(EXTRA_TEMPERATURE),
        "max_output_tokens": extras.get(EXTRA_MAX_OUTPUT_TOKENS),
        "defaults": default_ai_settings(),
        "masked_placeholder": MASKED_KEY_PLACEHOLDER_FA,
        "settings_url": f"/api/projects/{project_id}/settings/{workshop.slug}",
    }


# ---------------------------------------------------------------------------
# تبدیل به تنظیمات کارگاه تحلیل بودجه
# ---------------------------------------------------------------------------
def to_budget_llm_settings(settings: AISettings):
    """``AISettings`` خنثا را به تنظیمات لایه‌ی LLM کارگاه تحلیل بودجه تبدیل می‌کند."""
    from budget_analysis.llm import BudgetLLMSettings

    return BudgetLLMSettings(
        api_key=settings.api_key,
        base_url=settings.api_url,
        model=settings.model,
        temperature=settings.temperature,
        max_output_tokens=settings.max_output_tokens,
        request_timeout=settings.request_timeout,
        max_retries=settings.max_retries,
        retry_backoff_seconds=settings.retry_backoff_seconds,
    )


def overlay(settings: AISettings, **overrides: Any) -> AISettings:
    """نسخه‌ای با چند مقدار جایگزین -- برای «تست اتصال» پیش از ذخیره.

    مقادیر خالی/``None`` نادیده گرفته می‌شوند تا فرم نیمه‌پر، تست معتبر بدهد.
    """
    clean = {key: value for key, value in overrides.items() if value not in (None, "")}
    return replace(settings, **clean) if clean else settings
