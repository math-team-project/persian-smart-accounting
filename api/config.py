"""
تنظیمات سطح-API با استفاده از pydantic-settings.

نکته مهم: pipeline.py و audit_report_generator همچنان متغیرهای محیطی مربوط به
کلیدهای LLM را مستقیماً از طریق ``os.getenv``/``os.environ`` (و ``python-dotenv``)
می‌خوانند -- دقیقاً مثل قبل، بدون هیچ تغییری. این فایل آن رفتار را عوض نمی‌کند؛
فقط همان نام متغیرهای محیطی مستندشده در README (بخش Configuration) را برای
مصرف‌کنندگان دیگر لایه API (مثل نمایش وضعیت پیکربندی) در یک مکان واحد در دسترس
می‌گذارد و تنظیمات مخصوص خود لایه API (اندازه‌ی مجاز آپلود، عمر job و...) را
تعریف می‌کند.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"

# کلید پیش‌فرض امضای کوکی نشست. برای اجرای محلی/دموی یک‌نفره کافی است، اما در
# استقرار واقعی باید ``PSA_SECRET_KEY`` تنظیم شود؛ در غیر این صورت با ری‌استارت
# سرور همه‌ی نشست‌ها باطل می‌شوند و هر کسی که این مقدار پیش‌فرض را بداند می‌تواند
# کوکی نشست جعل کند. ``api/main.py`` هنگام بالا آمدن درباره‌ی آن هشدار می‌دهد.
DEFAULT_SECRET_KEY = "psa-dev-secret-key-change-me"


def _sqlite_url(path: Path) -> str:
    # مسیر مطلق با اسلش رو به جلو؛ SQLAlchemy فرم ``sqlite:///C:/...`` را در ویندوز
    # به‌درستی باز می‌کند (برخلاف مسیر ویندوزی با بک‌اسلش).
    return f"sqlite:///{path.as_posix()}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # --- متغیرهای موجود که توسط pipeline.py / audit_report_generator خوانده
    # می‌شوند (جدول Configuration در README). این‌ها صرفاً برای اطلاع/اعتبارسنجی
    # این‌جا اعلام شده‌اند؛ خودِ pipeline همچنان مستقیماً os.environ را می‌خواند. ---
    b_ai_api_key: str | None = Field(default=None, alias="B_AI_API_KEY")
    api_key_openrouter: str | None = Field(default=None, alias="API_KEY_OPENROUTER")
    audit_report_llm_api_key: str | None = Field(default=None, alias="AUDIT_REPORT_LLM_API_KEY")
    audit_report_llm_base_url: str = Field(
        default="https://openrouter.ai/api/v1", alias="AUDIT_REPORT_LLM_BASE_URL"
    )
    audit_report_llm_model: str = Field(
        default="nvidia/nemotron-3-ultra-550b-a55b:free", alias="AUDIT_REPORT_LLM_MODEL"
    )
    tesseract_cmd: str | None = Field(default=None, alias="TESSERACT_CMD")

    # --- تنظیمات مخصوص لایه API (جدید در فاز ۱) ---
    max_upload_mb: int = Field(default=25, alias="PSA_MAX_UPLOAD_MB")
    job_ttl_seconds: int = Field(default=3600, alias="PSA_JOB_TTL_SECONDS")
    log_level: str = Field(default="INFO", alias="PSA_LOG_LEVEL")

    # --- تنظیمات لایه‌ی داشبورد/پروژه‌ها (فاز جدید: ورود، پروژه‌ها، تاریخچه) ---
    # پایگاه‌داده‌ی خودِ داشبورد (SQLite/SQLAlchemy) -- کاملاً جدا از PostgreSQL
    # مربوط به db_management/ که دست‌نخورده باقی مانده است.
    database_url: str = Field(
        default_factory=lambda: _sqlite_url(DATA_DIR / "psa.db"),
        alias="PSA_DB_URL",
    )
    # فایل‌های نتیجه‌ی هر اجرا (مثلاً گزارش Word) این‌جا نگه داشته می‌شوند؛ هرگز
    # فایل ورودی کاربر. با حذف پروژه، پوشه‌ی همان پروژه هم پاک می‌شود.
    results_dir: Path = Field(default=DATA_DIR / "results", alias="PSA_RESULTS_DIR")
    secret_key: str = Field(default=DEFAULT_SECRET_KEY, alias="PSA_SECRET_KEY")
    session_max_age_seconds: int = Field(default=14 * 24 * 3600, alias="PSA_SESSION_MAX_AGE")

    # کلید رمزنگاری کلیدهای API ذخیره‌شده‌ی هر پروژه/کارگاه (اختیاری اما توصیه‌شده).
    # یک کلید معتبر Fernet است؛ ساخت آن:
    #   python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
    # اگر تنظیم نشود، یک کلید پایدار از ``PSA_SECRET_KEY`` مشتق می‌شود (هیچ‌وقت
    # متن خام ذخیره نمی‌شود) -- اما در آن حالت تغییر ``PSA_SECRET_KEY`` کلیدهای
    # ذخیره‌شده را غیرقابل‌رمزگشایی می‌کند و کاربر باید آن‌ها را دوباره وارد کند.
    ai_settings_encryption_key: str | None = Field(
        default=None, alias="PSA_AI_SETTINGS_ENCRYPTION_KEY"
    )

    # --- زیرساخت پایگاه‌دانش (Knowledge Base) برای ``rag_chat_module`` --------
    # این تنظیمات فقط زیرساخت هستند و هنوز به هیچ کارگاهی وصل نشده‌اند (به کارگاه
    # آینده‌ی «دستیار مالی» -- financial_chatbot -- در فاز بعد وصل می‌شوند).
    # ریشه‌ی ذخیره‌سازی دیسک ایندکس‌های برداری (Chroma) هر پایگاه‌دانش؛ ساختار:
    # ``{root}/{user_id}/{project_id}/{kb_id}/``. با حذف پروژه (فاز بعد) کل
    # زیرپوشه‌ی همان پروژه پاک می‌شود.
    vector_store_root: Path = Field(
        default=DATA_DIR / "vector_stores", alias="PSA_VECTOR_STORE_ROOT"
    )
    # تعداد پایگاه‌دانش‌های آماده/ناموفقی که به‌ازای هر پروژه نگه داشته می‌شوند؛
    # مابقی «قدیمی‌تر از سیاست نگهداری» محسوب می‌شوند (حذف واقعی در فاز بعد است --
    # این‌جا فقط پرس‌وجوی شمارش/فهرست ساخته می‌شود).
    kb_retention_count: int = Field(default=3, alias="PSA_KB_RETENTION_COUNT")
    # مدل embedding مورد استفاده برای ساخت پایگاه‌دانش‌های جدید. مدل‌ها روی دیسک
    # کش می‌شوند و معمولاً ثابت می‌مانند، اما هر پایگاه‌دانش دقیقاً ثبت می‌کند با
    # کدام مدل ساخته شده است (ستون ``embedding_model``).
    embedding_model: str = Field(
        default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        alias="PSA_EMBEDDING_MODEL",
    )

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def using_default_secret_key(self) -> bool:
        return self.secret_key == DEFAULT_SECRET_KEY


@lru_cache
def get_settings() -> Settings:
    return Settings()
