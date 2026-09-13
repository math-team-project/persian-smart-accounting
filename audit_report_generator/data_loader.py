"""بارگذاری و اعتبارسنجی ورودی‌های خام (متن گزارش حسابرسی + جیسون چک‌لیست).

ورودی جیسون می‌تواند به یکی از سه شکل زیر باشد (هر سه پشتیبانی می‌شوند):

1. دیکشنری کامل خروجی موتور چک‌لیست: ``{"summary": {...}, "checklist": [...]}``
2. فقط لیست موارد (خواه فیلترشده روی FALSE باشد، خواه شامل همه وضعیت‌ها):
   ``[{"question_id": "Q1", "status": "FALSE", ...}, ...]``
3. رشته JSON معتبر معادل هر یک از دو حالت بالا.

در هر حالت، پکیج به‌صورت خودکار فقط مواردی با ``status == "FALSE"`` را انتخاب
می‌کند (case-insensitive) تا حتی اگر به‌اشتباه چک‌لیست کامل پاس داده شود،
گزارش نهایی فقط شامل موارد عدم تطابق باشد.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Union

from .exceptions import InputValidationError
from .schemas import ChecklistItem, LoadedInput

logger = logging.getLogger(__name__)

JSONLike = Union[str, Dict[str, Any], List[Any]]

_MAX_KEY_FIGURES_PER_ITEM = 6


def _parse_json_input(checklist_json: JSONLike) -> Union[Dict[str, Any], List[Any]]:
    if isinstance(checklist_json, (dict, list)):
        return checklist_json
    if isinstance(checklist_json, str):
        try:
            return json.loads(checklist_json)
        except json.JSONDecodeError as exc:
            raise InputValidationError(
                f"ورودی checklist_json یک رشته است اما JSON معتبر نیست: {exc}"
            ) from exc
    raise InputValidationError(
        "checklist_json باید dict، list یا رشته JSON باشد؛ "
        f"نوع دریافت‌شده: {type(checklist_json).__name__}"
    )


def _extract_items_list(parsed: Union[Dict[str, Any], List[Any]]) -> List[Dict[str, Any]]:
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        if "checklist" in parsed and isinstance(parsed["checklist"], list):
            return parsed["checklist"]
        # شاید خود دیکشنری یک آیتم تکی باشد
        if "question_id" in parsed or "question_text" in parsed:
            return [parsed]
        raise InputValidationError(
            "دیکشنری checklist_json نه کلید 'checklist' دارد و نه یک آیتم تکی "
            "چک‌لیست است. کلیدهای موجود: " + ", ".join(parsed.keys())
        )
    raise InputValidationError("ساختار checklist_json قابل تفسیر نیست.")


def _shorten_key_figures(extracted_data: Any) -> List[Dict[str, str]]:
    """چند مقدار کلیدی اول را برای درج در گزارش نگه می‌دارد تا پرامپت طولانی نشود."""
    if not isinstance(extracted_data, list):
        return []
    result = []
    for row in extracted_data[:_MAX_KEY_FIGURES_PER_ITEM]:
        if not isinstance(row, dict):
            continue
        name = row.get("متغیر") or row.get("variable") or row.get("name")
        value = row.get("مقدار استخراج‌شده") or row.get("value")
        if name is not None and value is not None:
            result.append({"name": str(name), "value": str(value)})
    return result


def _to_checklist_item(raw: Dict[str, Any], index: int) -> ChecklistItem:
    question_id = str(raw.get("question_id") or f"Q{index + 1}")
    question_text = (raw.get("question_text") or "").strip()
    if not question_text:
        raise InputValidationError(
            f"آیتم چک‌لیست با شناسه '{question_id}' فاقد question_text است."
        )
    message = (raw.get("message") or "").strip()
    purpose = raw.get("question_purpose") or ""
    status = str(raw.get("status") or "FALSE").upper()
    return ChecklistItem(
        question_id=question_id,
        question_text=question_text,
        message=message,
        question_purpose=purpose.strip(),
        status=status,
        key_figures=_shorten_key_figures(raw.get("extracted_data")),
        raw=raw,
    )


def load_inputs(doc_text: Optional[str], checklist_json: JSONLike) -> LoadedInput:
    """نقطه ورود اصلی: doc_text و checklist_json خام را اعتبارسنجی و به LoadedInput تبدیل می‌کند.

    ``doc_text`` اختیاری است: اگر کاربر گزارش حسابرسی نداشته باشد یا آن را ارسال نکند
    (``None`` یا رشته‌ی خالی)، گزارش نهایی صرفاً بر اساس نتایج چک‌لیست ساخته می‌شود و
    بخش «سایر نکات مهم گزارش حسابرسی» به‌طور کامل از خروجی حذف خواهد شد.

    Raises
    ------
    InputValidationError
        اگر checklist_json ساختار نامعتبر داشته باشد یا هیچ آیتم قابل‌فهمی در آن یافت نشود.
        (خالی/نبودن doc_text دیگر خطا محسوب نمی‌شود.)
    """
    
    has_audit_report = isinstance(doc_text, str) and bool(doc_text.strip())
    clean_doc_text = doc_text.strip() if has_audit_report else ""
    
    
    parsed = _parse_json_input(checklist_json)

    summary: Dict[str, Any] = {}
    if isinstance(parsed, dict) and isinstance(parsed.get("summary"), dict):
        summary = parsed["summary"]

    raw_items = _extract_items_list(parsed)
    if not isinstance(raw_items, list):
        raise InputValidationError("لیست آیتم‌های چک‌لیست قابل استخراج نبود.")

    non_compliant: List[ChecklistItem] = []
    skipped = 0
    for i, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            skipped += 1
            logger.warning("آیتم شماره %s در checklist نادیده گرفته شد (dict نیست).", i)
            continue
        status = str(raw_item.get("status") or "FALSE").upper()
        if status != "FALSE":
            # اگر کاربر از قبل فقط موارد FALSE را فیلتر کرده، این شرط همیشه True است.
            continue
        try:
            non_compliant.append(_to_checklist_item(raw_item, i))
        except InputValidationError as exc:
            skipped += 1
            logger.warning("آیتم شماره %s نادیده گرفته شد: %s", i, exc)

    if skipped:
        logger.warning("%s آیتم به دلیل ساختار نامعتبر از پردازش کنار گذاشته شدند.", skipped)

    return LoadedInput(
        doc_text=clean_doc_text,
        has_audit_report=has_audit_report,
        all_items_count=summary.get("total"),
        true_count=summary.get("true_count"),
        false_count=summary.get("false_count", len(non_compliant)),
        error_count=summary.get("error_count"),
        manual_count=summary.get("manual_count"),
        compliance_rate=summary.get("compliance_rate"),
        non_compliant_items=non_compliant,
    )
