"""تعریف استثناهای اختصاصی پکیج تا خطاها در سطح مناسب و با پیام روشن مدیریت شوند."""


class AuditReportError(Exception):
    """کلاس پایه برای تمام خطاهای این پکیج."""


class InputValidationError(AuditReportError):
    """ورودی‌ها (متن گزارش حسابرسی / جیسون چک‌لیست) نامعتبر یا ناقص هستند."""


class LLMGenerationError(AuditReportError):
    """خطا در ارتباط با مدل زبانی یا در تجزیه خروجی آن، پس از اتمام تلاش‌های مجاز."""


class DocxBuildError(AuditReportError):
    """خطا هنگام ساخت یا ذخیره‌سازی فایل خروجی Word."""
