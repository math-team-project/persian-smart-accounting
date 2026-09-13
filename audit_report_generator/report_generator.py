"""هماهنگ‌کننده‌ی کل فرآیند: بارگذاری ورودی → پرامپت → LLM → ساخت گزارش Word.

از این نسخه به بعد، گزارش خروجی از **دو بخش** تشکیل می‌شود:

1. ``checklist_items`` — همان خلاصه‌ی موارد عدم تطابق شناسایی‌شده توسط چک‌لیست خودکار.
2. ``audit_report_findings`` — نکات مهمی که مستقیماً از مرور کامل متن گزارش حسابرسی
   مستقل استخراج می‌شوند و در چک‌لیست خودکار اصلاً پوشش داده نشده‌اند (مثل بند شرط/تاکید،
   ابهام در تداوم فعالیت، محدودیت رسیدگی و ...). این بخش تضمین می‌کند گزارش نهایی صرفاً
   محدود به سوالات از پیش تعریف‌شده‌ی چک‌لیست نماند.

هر دو بخش در **یک فراخوانی واحد** LLM درخواست می‌شوند (برای هماهنگی لحن و کاهش هزینه)،
اما هرکدام به‌طور مستقل اعتبارسنجی/fallback می‌شوند تا نقص در یک بخش، بخش دیگر را
خراب نکند.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from .config import LLMConfig
from .data_loader import load_inputs
from .docx_builder import build_docx
from .exceptions import DocxBuildError, LLMGenerationError
from .llm_client import LLMClient
from .prompt_builder import build_checklist_only_messages, build_messages
from .schemas import AuditFinding, ChecklistItem, LoadedInput, ReportItem

logger = logging.getLogger(__name__)

_VALID_SEVERITIES = {"کم", "متوسط", "بالا"}


# --------------------------------------------------------------------------
# fallback ها (زمانی استفاده می‌شوند که LLM در دسترس نباشد یا خروجی ناقص بدهد)
# --------------------------------------------------------------------------

def _fallback_report_item(item: ChecklistItem) -> ReportItem:
    """در صورت شکست LLM برای یک مورد خاص از چک‌لیست، یک خلاصه‌ی حداقلیِ مبتنی بر
    داده‌های خام می‌سازیم تا آن مورد به‌طور کامل از گزارش حذف نشود."""
    summary = item.message or item.question_text
    return ReportItem(
        question_id=item.question_id,
        title=(item.question_text[:60] + "...") if len(item.question_text) > 60 else item.question_text,
        summary=summary,
        committee_focus="این مورد نیاز به بررسی و اظهار نظر حسابرس در جلسه دارد.",
        mentioned_in_audit_report=False,
        severity="متوسط",
    )


# --------------------------------------------------------------------------
# تبدیل خروجی خام LLM به ساختارهای typed، با اعتبارسنجی و fallback مستقل
# --------------------------------------------------------------------------

def _coerce_report_items(
    raw_items: object, items: List[ChecklistItem]
) -> List[ReportItem]:
    """خروجی خام بخش checklist_items را اعتبارسنجی و به ReportItem تبدیل می‌کند؛
    هر مورد ناقص/گمشده با نسخه‌ی fallback جایگزین می‌شود تا کل فرآیند به‌خاطر یک
    آیتم خراب متوقف نشود."""

    if raw_items is None:
        raw_items = []
    if not isinstance(raw_items, list):
        logger.warning(
            "بخش checklist_items در خروجی LLM از نوع %s بود، نه لیست؛ از fallback استفاده می‌شود.",
            type(raw_items).__name__,
        )
        raw_items = []

    by_id = {}
    for entry in raw_items:
        if not isinstance(entry, dict) or "question_id" not in entry:
            logger.warning("یک عضو نامعتبر در checklist_items نادیده گرفته شد: %r", entry)
            continue
        by_id[str(entry["question_id"])] = entry

    results: List[ReportItem] = []
    for item in items:
        entry = by_id.get(item.question_id)
        if entry is None:
            logger.warning(
                "خروجی LLM برای question_id=%s وجود نداشت؛ از خلاصه‌ی جایگزین استفاده می‌شود.",
                item.question_id,
            )
            results.append(_fallback_report_item(item))
            continue

        title = str(entry.get("title") or item.question_text[:60]).strip()
        summary = str(entry.get("summary") or item.message or item.question_text).strip()
        committee_focus = str(
            entry.get("committee_focus") or "این مورد نیاز به بررسی در جلسه دارد."
        ).strip()
        mentioned = bool(entry.get("mentioned_in_audit_report", False))
        severity = str(entry.get("severity") or "متوسط").strip()
        if severity not in _VALID_SEVERITIES:
            severity = "متوسط"

        results.append(
            ReportItem(
                question_id=item.question_id,
                title=title,
                summary=summary,
                committee_focus=committee_focus,
                mentioned_in_audit_report=mentioned,
                severity=severity,
            )
        )

    return results


def _coerce_audit_findings(raw_findings: object) -> List[AuditFinding]:
    """خروجی خام بخش audit_report_findings را اعتبارسنجی و به AuditFinding تبدیل
    می‌کند. برخلاف checklist_items، اینجا مبنای مرجعی برای «تعداد مورد انتظار» نداریم
    (چون این بخش مستقیماً حاصل مرور آزاد متن گزارش توسط مدل است)؛ بنابراین صرفاً
    ورودی‌های بدشکل نادیده گرفته می‌شوند، نه این‌که برایشان fallback ساخته شود."""

    if raw_findings is None:
        return []
    if not isinstance(raw_findings, list):
        logger.warning(
            "بخش audit_report_findings در خروجی LLM از نوع %s بود، نه لیست؛ نادیده گرفته شد.",
            type(raw_findings).__name__,
        )
        return []

    results: List[AuditFinding] = []
    for entry in raw_findings:
        if not isinstance(entry, dict):
            logger.warning("یک عضو نامعتبر در audit_report_findings نادیده گرفته شد: %r", entry)
            continue
        title = str(entry.get("title") or "").strip()
        summary = str(entry.get("summary") or "").strip()
        if not title or not summary:
            logger.warning(
                "یک نکته در audit_report_findings فاقد title/summary بود و نادیده گرفته شد: %r",
                entry,
            )
            continue
        committee_focus = str(
            entry.get("committee_focus") or "این نکته نیاز به توجه/تصمیم‌گیری در جلسه دارد."
        ).strip()
        severity = str(entry.get("severity") or "متوسط").strip()
        if severity not in _VALID_SEVERITIES:
            severity = "متوسط"
        source_hint = str(entry.get("source_hint") or "").strip()

        results.append(
            AuditFinding(
                title=title,
                summary=summary,
                committee_focus=committee_focus,
                severity=severity,
                source_hint=source_hint,
            )
        )

    return results


class ReportGenerator:
    """کلاس اصلی؛ برای استفاده‌ی مکرر (مثلاً چند فایل پشت‌سرهم) بهتر از تابع تک‌کاره است
    چون کلاینت LLM یک‌بار ساخته می‌شود."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = LLMClient(config)

    def generate(
        self,
        checklist_json,
        output_path: str,
        doc_text: Optional[str] = None,
        report_title: str = "گزارش موارد عدم تطابق چک‌لیست حسابرسی جهت جلسه کمیسیون",
        entity_name: Optional[str] = None,
    ) -> str:
        """کل فرآیند را اجرا و فایل docx را در ``output_path`` می‌نویسد.

        ``doc_text`` اختیاری است. اگر کاربر گزارش حسابرسی نداشته باشد، آن را خالی
        بگذارید یا اصلاً پاس ندهید (``None``)؛ در این حالت:
          - LLM فقط برای خلاصه‌سازی خودِ موارد چک‌لیست فراخوانی می‌شود (بدون تلاش برای
            تحلیل گزارش حسابرسی).
          - بخش «سایر نکات مهم گزارش حسابرسی» به‌طور کامل از فایل خروجی حذف می‌شود.

        اگر ``doc_text`` ارائه شود، رفتار قبلی (پوشش کامل هر دو بخش) اجرا می‌شود.

        Returns
        -------
        str
            مسیر فایل docx ساخته‌شده.
        """
        loaded: LoadedInput = load_inputs(doc_text, checklist_json)

        messages = build_messages(loaded) if loaded.has_audit_report else build_checklist_only_messages(loaded)
        audit_findings_error: Optional[str] = None
        try:
            raw_response = self.client.chat_json(messages)

            if isinstance(raw_response, dict):
                raw_checklist_items = raw_response.get("checklist_items")
                raw_audit_findings = raw_response.get("audit_report_findings") if loaded.has_audit_report else None
            elif isinstance(raw_response, list):
                # سازگاری با قرارداد قدیمی‌تر (فقط آرایه‌ی checklist)؛ در این حالت
                # بخش دوم قابل استخراج نیست.
                logger.warning(
                    "خروجی LLM یک لیست خام بود (قرارداد قدیمی)؛ بخش audit_report_findings خالی خواهد بود."
                )
                raw_checklist_items = raw_response
                raw_audit_findings = None
            else:
                raise LLMGenerationError(
                    f"خروجی LLM از نوع {type(raw_response).__name__} بود، در حالی که یک شیء JSON انتظار می‌رفت."
                )

            report_items = _coerce_report_items(raw_checklist_items, loaded.non_compliant_items)
            audit_findings = _coerce_audit_findings(raw_audit_findings) if loaded.has_audit_report else []

        except LLMGenerationError as exc:
            logger.error(
                "تولید خلاصه توسط LLM ناموفق بود؛ گزارش با خلاصه‌های پایه (بدون LLM) ساخته می‌شود. خطا: %s",
                exc,
            )
            report_items = [_fallback_report_item(item) for item in loaded.non_compliant_items]
            audit_findings = []
            if loaded.has_audit_report:
                audit_findings_error = (
                    "تحلیل نکات اضافی گزارش حسابرسی (خارج از چک‌لیست) به دلیل خطا در سرویس "
                    "هوش مصنوعی در این اجرا انجام نشد."
                )

        try:
            path = build_docx(
                loaded=loaded,
                report_items=report_items,
                audit_findings=audit_findings,
                output_path=output_path,
                report_title=report_title,
                entity_name=entity_name,
                audit_findings_error=audit_findings_error,
                has_audit_report=loaded.has_audit_report,
            )
        except Exception as exc:  # noqa: BLE001 - می‌خواهیم هر خطای docx را یکدست کنیم
            raise DocxBuildError(f"ساخت فایل Word با خطا مواجه شد: {exc}") from exc

        return path


def generate_committee_report(
    checklist_json,
    config: LLMConfig,
    output_path: str = "output/گزارش_کمیسیون.docx",
    doc_text: Optional[str] = None,
    report_title: str = "گزارش موارد عدم تطابق چک‌لیست حسابرسی جهت جلسه کمیسیون",
    entity_name: Optional[str] = None,
) -> str:
    """تابع سطح‌بالا برای استفاده‌ی یک‌باره (بدون نیاز به ساخت دستی ReportGenerator).

    Parameters
    ----------
    checklist_json:
        خروجی جیسون چک‌لیست (dict کامل، لیست موارد FALSE، یا رشته JSON معادل آن‌ها).
        می‌تواند خالی هم باشد (یعنی چک‌لیست موردی نداشته).
    config:
        تنظیمات اتصال به LLM (``LLMConfig``).
    output_path:
        مسیر فایل خروجی docx.
    doc_text:
        متن استخراج‌شده از فایل گزارش حسابرسی (doc/docx)، **اختیاری**. اگر کاربر
        گزارش حسابرسی نداشت، این پارامتر را ندهید (یا ``None``/رشته خالی بدهید)؛
        در این حالت گزارش نهایی صرفاً بر اساس نتایج چک‌لیست ساخته می‌شود.
    report_title:
        عنوان اصلی گزارش.
    entity_name:
        نام سازمان/شرکت (اختیاری) برای درج در سربرگ گزارش.

    Returns
    -------
    str
        مسیر فایل docx ساخته‌شده.
    """
    generator = ReportGenerator(config)
    return generator.generate(
        doc_text=doc_text,
        checklist_json=checklist_json,
        output_path=output_path,
        report_title=report_title,
        entity_name=entity_name,
    )
