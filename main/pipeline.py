"""
هسته پردازشی داشبورد هوشمند حسابرسی.

این ماژول همان گردش‌کاری را اجرا می‌کند که در main/main.ipynb تعریف شده است:
    1) استخراج فرم‌های بودجه (اصلاحیه / ابلاغ / تاییدیه) با
       extraction_script.scripts.xlsx.budget.budget_process
    2) استخراج صورت‌های مالی با
       extraction_script.scripts.xlsx.financial_statements.process
    3) بارگذاری تراز آزمایشی (ترازنامه) با pandas
    4) ادغام همه‌ی شیت‌ها در یک دیکشنری واحد (IMPORTED_DF) دقیقا مثل نوت‌بوک
    5) اجرای تمام سوالات چک‌لیست حسابرسی با
       extraction_script.scripts.checklist.checklist_process

خروجی هر سوال، ساختاری منطبق با مستندات checklist_process.md دارد:
question_id / general_description / question_purpose / evaluation_condition /
evaluation_breakdown / extracted_data / status (TRUE | FALSE | ERROR | MANUAL)
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pandas as pd

# ---------------------------------------------------------------------------
# اضافه کردن مسیرهای لازم به sys.path -- دقیقا مطابق سلول اول main.ipynb --
# تا وارد کردن ماژول‌های extraction_script مستقل از دایرکتوری اجرا کار کند.
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent
_PATHS_TO_ADD = [
    ROOT_DIR,
    ROOT_DIR / "extraction_script" / "scripts",
    ROOT_DIR / "extraction_script" / "scripts" / "xlsx",
    ROOT_DIR / "extraction_script" / "scripts" / "xlsx" / "financial_statements",
]
for _p in _PATHS_TO_ADD:
    _p_str = str(_p)
    if _p_str not in sys.path:
        sys.path.insert(0, _p_str)

from extraction_script.scripts.checklist.checklist_process import (  # noqa: E402
    load_excels_to_ram,
    run_audit_pipeline,
)
from extraction_script.scripts.xlsx.budget.budget_process import (  # noqa: E402
    main as run_budget_extraction,
)
from extraction_script.scripts.xlsx.financial_statements.process import (  # noqa: E402
    main as run_financial_statements_extraction,
)

CHECKLIST_HANDLER_PATH = (
    ROOT_DIR / "extraction_script" / "data" / "Checklist_Question_extracted_handler.json"
)

# تعریف اسلات‌های آپلود فایل مورد استفاده در رابط کاربری
# مقدار "icon" نام یک آیکون در main/icons.py است (نه ایموجی).
FILE_SLOTS: dict[str, dict[str, Any]] = {
    "revised_budget": {
        "label": "فایل بودجه اصلاحیه",
        "help": "فرم‌های بودجه تفصیلی اصلاحیه (فرم ۱ تا ۱۰)",
        "required": True,
        "icon": "file-spreadsheet",
    },
    "financial_statements": {
        "label": "فایل صورت‌های مالی",
        "help": "صورت وضعیت مالی، صورت تغییرات و یادداشت‌های توضیحی",
        "required": True,
        "icon": "file-text",
    },
    "balance_sheet": {
        "label": "فایل ترازنامه",
        "help": "تراز آزمایشی (تراز کل / معین)",
        "required": True,
        "icon": "database",
    },
    "credit_approvals": {
        "label": "فایل تاییدیه اعتبارات",
        "help": "تاییدیه اعتبارات هزینه‌ای، اختصاصی و تملک دارایی‌های سرمایه‌ای",
        "required": False,
        "icon": "file-down",
    },
    "budget_law": {
        "label": "فایل قانون بودجه",
        "help": "ابلاغ بودجه مصوب سازمان برنامه و بودجه",
        "required": False,
        "icon": "file-down",
    },
}

# رنگ‌ها بر اساس پالت حالت تیره (Dark Mode) طراحی رابط کاربری
STATUS_META = {
    "TRUE": {"label": "تطابق دارد", "color": "#10B981", "icon": "check-circle"},
    "FALSE": {"label": "عدم تطابق", "color": "#EF4444", "icon": "x-circle"},
    "ERROR": {"label": "خطای پردازش", "color": "#F59E0B", "icon": "alert-triangle"},
    "MANUAL": {"label": "نیازمند بررسی دستی", "color": "#64748B", "icon": "eye"},
}


class PipelineError(Exception):
    """خطای سطح بالا برای مشکلات مراحل پردازش که باید مستقیما به کاربر نمایش داده شود."""


# ---------------------------------------------------------------------------
# ابزارهای کمکی فایل
# ---------------------------------------------------------------------------

def _is_xls(path: Path) -> bool:
    return path.suffix.lower() == ".xls"


def _convert_xls_to_xlsx(src_path: Path, dest_dir: Path) -> Path:
    """
    تبدیل فایل اکسل قدیمی (.xls) به .xlsx به همراه پر کردن سلول‌های ادغام‌شده.
    ماژول‌های استخراج بودجه/صورت‌های مالی صرفا از openpyxl (یعنی فرمت xlsx)
    پشتیبانی می‌کنند، بنابراین فایل‌های xls پیش از پردازش تبدیل می‌شوند.
    """
    try:
        import xlrd
        from openpyxl import Workbook
    except ImportError as exc:  # pragma: no cover
        raise PipelineError(
            "برای پردازش فایل‌های با فرمت xls، کتابخانه xlrd نصب نیست."
        ) from exc

    book = xlrd.open_workbook(str(src_path), formatting_info=False)
    out_wb = Workbook()
    out_wb.remove(out_wb.active)

    for sheet in book.sheets():
        grid = [
            [sheet.cell_value(r, c) for c in range(sheet.ncols)]
            for r in range(sheet.nrows)
        ]
        for merged in getattr(sheet, "merged_cells", []):
            rlo, rhi, clo, chi = merged
            top_value = grid[rlo][clo]
            for r in range(rlo, rhi):
                for c in range(clo, chi):
                    grid[r][c] = top_value

        safe_name = (sheet.name or "Sheet").strip()[:31] or "Sheet"
        ws = out_wb.create_sheet(title=safe_name)
        for row in grid:
            ws.append([v if v != "" else None for v in row])

    dest_path = dest_dir / f"{src_path.stem}__converted.xlsx"
    out_wb.save(dest_path)
    return dest_path


def save_uploaded_file(uploaded_file, dest_dir: Path) -> Path:
    """ذخیره یک فایل آپلودشده استریم‌لیت روی دیسک (با تبدیل خودکار xls به xlsx)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    raw_path = dest_dir / uploaded_file.name
    with open(raw_path, "wb") as fh:
        fh.write(uploaded_file.getbuffer())

    if _is_xls(raw_path):
        try:
            return _convert_xls_to_xlsx(raw_path, dest_dir)
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(
                f"تبدیل فایل «{uploaded_file.name}» از xls به xlsx ناموفق بود: {exc}"
            ) from exc
    return raw_path


def make_temp_workdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="psa_dashboard_"))


def cleanup_workdir(path: Optional[Path]) -> None:
    if path is not None:
        shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# مرحله ۱: استخراج و ادغام داده‌ها (معادل سلول اول main.ipynb)
# ---------------------------------------------------------------------------

@dataclass
class ProcessedInputs:
    imported_sheets: dict[str, Any]
    sheet_source_counts: dict[str, int]
    warnings: list[str] = field(default_factory=list)


def build_imported_sheets(file_paths: dict[str, Optional[Path]]) -> ProcessedInputs:
    """اجرای استخراج‌کننده‌های تخصصی روی فایل‌های آپلودشده و ساخت IMPORTED_DF یکپارچه."""
    imported: dict[str, Any] = {}
    counts: dict[str, int] = {}
    warnings: list[str] = []

    revised_path = file_paths.get("revised_budget")
    if not revised_path:
        raise PipelineError("فایل «بودجه اصلاحیه» الزامی است و ارسال نشده است.")
    revised_budget = run_budget_extraction(
        budget_type="اصلاحیه", excel_file_path={"اصلاحیه": str(revised_path)}
    )
    imported.update(revised_budget)
    counts["بودجه اصلاحیه"] = len(revised_budget)

    fs_path = file_paths.get("financial_statements")
    if not fs_path:
        raise PipelineError("فایل «صورت‌های مالی» الزامی است و ارسال نشده است.")
    financial_statements = run_financial_statements_extraction(str(fs_path))
    imported.update(financial_statements)
    counts["صورت‌های مالی"] = len(financial_statements)

    bs_path = file_paths.get("balance_sheet")
    if not bs_path:
        raise PipelineError("فایل «ترازنامه» الزامی است و ارسال نشده است.")
    raw_taraz = pd.read_excel(str(bs_path), sheet_name=None)
    taraz = {name: {"data": df} for name, df in raw_taraz.items()}
    imported.update(taraz)
    counts["ترازنامه"] = len(taraz)

    ca_path = file_paths.get("credit_approvals")
    if ca_path:
        try:
            tayidie = run_budget_extraction(
                budget_type="تاییدیه", excel_file_path={"تاییدیه": str(ca_path)}
            )
            imported.update(tayidie)
            counts["تاییدیه اعتبارات"] = len(tayidie)
        except Exception as exc:
            warnings.append(f"پردازش فایل «تاییدیه اعتبارات» با خطا مواجه شد: {exc}")

    bl_path = file_paths.get("budget_law")
    if bl_path:
        try:
            eblagh = run_budget_extraction(
                budget_type="ابلاغ", excel_file_path={"ابلاغ": str(bl_path)}
            )
            imported.update(eblagh)
            counts["قانون بودجه"] = len(eblagh)
        except Exception as exc:
            warnings.append(f"پردازش فایل «قانون بودجه» با خطا مواجه شد: {exc}")

    if not imported:
        raise PipelineError("هیچ شیتی از فایل‌های ارسالی قابل استخراج نبود.")

    return ProcessedInputs(imported_sheets=imported, sheet_source_counts=counts, warnings=warnings)


# ---------------------------------------------------------------------------
# مرحله ۲: اجرای چک‌لیست حسابرسی (معادل سلول سوم main.ipynb)
# ---------------------------------------------------------------------------

def load_checklist_definitions() -> list[dict[str, Any]]:
    with open(CHECKLIST_HANDLER_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh).get("audit_questions", [])


def _has_evaluation_logic(question: dict[str, Any]) -> bool:
    logic = question.get("evaluation_logic")
    if not logic:
        return False
    return bool(logic.get("condition") or logic.get("formula"))


def _format_extracted_data(extracted: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for name, value in (extracted or {}).items():
        rows.append({"متغیر": name, "مقدار استخراج‌شده": _format_value(value)})
    return rows


def _format_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        if value != value:  # NaN
            return "—"
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    if isinstance(value, list):
        return ", ".join(_format_value(v) for v in value) if value else "—"
    return str(value)


def run_checklist(imported_sheets: dict[str, Any]) -> list[dict[str, Any]]:
    """اجرای تمام سوالات چک‌لیست و بازگرداندن نتایج ساختاریافته برای نمایش در داشبورد."""
    questions = load_checklist_definitions()
    loaded_sheets = load_excels_to_ram(imported_sheets)

    results: list[dict[str, Any]] = []
    for question in questions:
        q_id = question["question_id"]
        evaluable = _has_evaluation_logic(question)

        record: dict[str, Any] = {
            "question_id": q_id,
            "question_text": question.get("question_text", ""),
            "question_purpose": question.get("question_porpose", ""),
            "general_description": question.get("general_descrintion", ""),
            "is_evaluable": evaluable,
            "evaluation_condition": None,
            "condition_breakdown": [],
            "extracted_data": [],
            "message": "",
            "status": "MANUAL",
        }

        if not evaluable:
            record["message"] = "این سوال منطق ارزیابی خودکار ندارد و نیازمند بررسی دستی حسابرس است."
            results.append(record)
            continue

        try:
            pipeline_result = run_audit_pipeline(
                q_id, str(CHECKLIST_HANDLER_PATH), None, preloaded_sheets=loaded_sheets
            )
        except Exception as exc:
            record["status"] = "ERROR"
            record["message"] = f"خطای اجرای پایپ‌لاین ارزیابی: {exc}"
            results.append(record)
            continue

        if not pipeline_result:
            record["status"] = "ERROR"
            record["message"] = "پاسخی از موتور ارزیابی دریافت نشد (احتمالا سوال در فایل JSON یافت نشد)."
            results.append(record)
            continue

        eval_result = pipeline_result.get("evaluation_result", {}) or {}
        status = eval_result.get("status", "ERROR")

        record["evaluation_condition"] = pipeline_result.get("evaluation_condition")
        record["extracted_data"] = _format_extracted_data(pipeline_result.get("extracted_data", {}))
        record["message"] = eval_result.get("message") or ""
        record["status"] = status if status in ("TRUE", "FALSE", "ERROR") else "ERROR"

        formatted_conditions = eval_result.get("formatted_conditions", []) or []
        breakdown = eval_result.get("breakdown", []) or []
        condition_breakdown = []
        for idx in range(max(len(formatted_conditions), len(breakdown))):
            cond = formatted_conditions[idx] if idx < len(formatted_conditions) else ""
            res = breakdown[idx] if idx < len(breakdown) else "FAILED"
            condition_breakdown.append({"condition": cond, "result": res})
        record["condition_breakdown"] = condition_breakdown

        results.append(record)

    return results


def summarize_checklist(results: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"TRUE": 0, "FALSE": 0, "ERROR": 0, "MANUAL": 0}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    evaluated = counts["TRUE"] + counts["FALSE"]
    compliance_rate = (counts["TRUE"] / evaluated * 100) if evaluated else 0.0
    return {
        "total": len(results),
        "true_count": counts["TRUE"],
        "false_count": counts["FALSE"],
        "error_count": counts["ERROR"],
        "manual_count": counts["MANUAL"],
        "compliance_rate": compliance_rate,
    }


# ---------------------------------------------------------------------------
# ابزار کمکی برای مرور داده‌های استخراج‌شده در رابط کاربری
# ---------------------------------------------------------------------------

def flatten_sheets_for_preview(imported_sheets: dict[str, Any]) -> dict[str, Any]:
    """
    تبدیل ساختار تودرتوی IMPORTED_DF (که می‌تواند dict یا DataFrame باشد) به یک
    دیکشنری تخت {نام قابل‌نمایش: DataFrame/متن} صرفا برای پیش‌نمایش در داشبورد.
    """
    flat: dict[str, Any] = {}
    for sheet_name, payload in imported_sheets.items():
        if isinstance(payload, pd.DataFrame):
            flat[sheet_name] = payload
            continue
        if isinstance(payload, dict):
            if "data" in payload and isinstance(payload["data"], pd.DataFrame):
                flat[sheet_name] = payload["data"]
                continue
            for sub_name, sub_payload in payload.items():
                key = f"{sheet_name} / {sub_name}"
                if isinstance(sub_payload, pd.DataFrame):
                    flat[key] = sub_payload
                elif isinstance(sub_payload, dict) and isinstance(sub_payload.get("data"), pd.DataFrame):
                    flat[key] = sub_payload["data"]
                else:
                    flat[key] = sub_payload
    return flat


# ---------------------------------------------------------------------------
# نقطه ورود اصلی خط پردازش
# ---------------------------------------------------------------------------

def run_full_pipeline(file_paths: dict[str, Optional[Path]]) -> dict[str, Any]:
    start = time.time()
    processed_inputs = build_imported_sheets(file_paths)
    checklist_results = run_checklist(processed_inputs.imported_sheets)
    summary = summarize_checklist(checklist_results)
    elapsed = time.time() - start
    return {
        "imported_sheets": processed_inputs.imported_sheets,
        "sheet_source_counts": processed_inputs.sheet_source_counts,
        "warnings": processed_inputs.warnings,
        "checklist_results": checklist_results,
        "summary": summary,
        "elapsed_seconds": elapsed,
    }
