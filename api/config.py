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

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
