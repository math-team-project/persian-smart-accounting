"""
هسته پردازشی داشبورد هوشمند حسابرسی.

این ماژول همان گردش‌کاری را اجرا می‌کند که در main.ipynb تعریف شده است:
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
import os
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
import pandas as pd
import warnings
warnings.filterwarnings('ignore')  # Suppress all warnings

# ---------------------------------------------------------------------------
# اضافه کردن مسیرهای لازم به sys.path -- دقیقا مطابق سلول اول main.ipynb --
# تا وارد کردن ماژول‌های extraction_script مستقل از دایرکتوری اجرا کار کند.
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent
_PATHS_TO_ADD = [
    ROOT_DIR,
    ROOT_DIR / "extraction_script" / "scripts",
    ROOT_DIR / "extraction_script" / "checklist",
    ROOT_DIR / "extraction_script" / "scripts" / "xlsx",
    ROOT_DIR / "extraction_script" / "scripts" / "xlsx" / "budget",
    ROOT_DIR / "extraction_script" / "scripts" / "xlsx" / "financial_statements",
    ROOT_DIR / "extraction_script" / "scripts" / "document_conversion",
]
for _p in _PATHS_TO_ADD:
    _p_str = str(_p)
    if _p_str not in sys.path:
        sys.path.insert(0, _p_str)

from extraction_script.scripts.checklist.checklist_process import (  # noqa: E402
    load_excels_to_ram,
    run_audit_pipeline,
)
from extraction_script.scripts.checklist.year_relabeler import (
    analyze_dataframes,
    apply_header_relabeling_to_many,
)
from extraction_script.scripts.xlsx.budget.budget_process import (  # noqa: E402
    main as run_budget_extraction,
    detect_config_by_content
)
from extraction_script.scripts.xlsx.financial_statements.process import (  # noqa: E402
    main as run_financial_statements_extraction,
)
from extraction_script.scripts.xlsx.budget.config import (
    CONTENT_KEYWORD_MAP
)
from extraction_script.scripts.document_conversion.pdf_to_excel_fa import (
    convert as pdf_converter
)
from extraction_script.scripts.document_conversion.file_to_text import (
    extract_text as extract_audit_report_text,
    ExtractionError as AuditReportExtractionError,
)

CHECKLIST_HANDLER_PATH = (
    ROOT_DIR / "extraction_script" / "data" / "Checklist_Question_extracted_handler.json"
)

# تعریف اسلات‌های آپلود فایل مورد استفاده در رابط کاربری
# مقدار "icon" نام یک آیکون در icons.py است (نه ایموجی).
FILE_SLOTS: dict[str, dict[str, Any]] = {
    "revised_budget": {
        "label": "فایل بودجه اصلاحیه",
        "help": "فرم‌های بودجه تفصیلی اصلاحیه (فرم ۱ تا ۱۰)",
        "required": True,
        "icon": "file-spreadsheet",
        "types": ["xlsx", "xls", "pdf"],
    },
    "financial_statements": {
        "label": "فایل صورت‌های مالی",
        "help": "صورت وضعیت مالی، صورت تغییرات و یادداشت‌های توضیحی",
        "required": True,
        "icon": "file-text",
        "types": ["xlsx", "xls", "pdf"],
    },
    "balance_sheet": {
        "label": "فایل ترازنامه",
        "help": "تراز آزمایشی (تراز کل / معین)",
        "required": True,
        "icon": "database",
        "types": ["xlsx", "xls", "pdf"],
    },
    "credit_approvals": {
        "label": "فایل تاییدیه اعتبارات",
        "help": "تاییدیه اعتبارات هزینه‌ای، اختصاصی و تملک دارایی‌های سرمایه‌ای",
        "required": False,
        "icon": "file-down",
        "types": ["xlsx", "xls", "pdf"],
    },
    "budget_law": {
        "label": "فایل قانون بودجه",
        "help": "ابلاغ بودجه مصوب سازمان برنامه و بودجه",
        "required": False,
        "icon": "file-down",
        "types": ["xlsx", "xls", "pdf"],
    },
    "audit_report_doc": {
        "label": "گزارش حسابرسی",
        "help": "متن گزارش حسابرسی (اختیاری، فرمت PDF/DOC/DOCX). در صورت بارگذاری، برای غنی‌سازی گزارش کمیسیون استفاده می‌شود؛ بارگذاری آن الزامی نیست.",
        "required": False,
        "icon": "file-text",
        "types": ["pdf", "doc", "docx"],
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

def _is_pdf(path: Path) -> bool:
    return path.suffix.lower() == ".pdf"


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
    """
    Saves a Streamlit uploaded file to disk and automatically converts
    legacy (.xls) and document (.pdf) formats to standard Excel (.xlsx).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    raw_path = dest_dir / uploaded_file.name

    # Save the raw uploaded buffer to disk
    with open(raw_path, "wb") as fh:
        fh.write(uploaded_file.getbuffer())

    # 1. Handle legacy Excel files (.xls conversion)
    if _is_xls(raw_path):
        try:
            return _convert_xls_to_xlsx(raw_path, dest_dir)
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(
                f"تبدیل فایل «{uploaded_file.name}» از xls به xlsx ناموفق بود: {exc}"
            ) from exc

    # 2. Handle PDF document files (.pdf conversion)
    if _is_pdf(raw_path):
        try:
            target_xlsx_path = raw_path.with_suffix(".xlsx")
            # Call your custom PDF converter module function
            converted_path_str = pdf_converter(pdf_path=str(raw_path), xlsx_path=str(target_xlsx_path))
            return Path(converted_path_str)
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(
                f"تبدیل فایل «{uploaded_file.name}» از PDF به xlsx ناموفق بود: {exc}"
            ) from exc

    return raw_path


def save_report_upload(uploaded_file, dest_dir: Path) -> Path:
    """
    Saves the (optional) uploaded audit report file (گزارش حسابرسی) to disk
    as-is, without running it through the xls/pdf -> xlsx conversion pipeline
    used for the structured (spreadsheet) upload slots. The raw file is later
    read with extract_audit_report_text().
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / uploaded_file.name
    with open(dest_path, "wb") as fh:
        fh.write(uploaded_file.getbuffer())
    return dest_path


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
        budget_type="اصلاحیه", excel_file_path={"اصلاحیه": str(revised_path)},
        content_keyword_map=CONTENT_KEYWORD_MAP.get("revised_budget")
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
    taraz = {}
    for original_sheet_name, df_sheet in raw_taraz.items():
        matched_content_key = detect_config_by_content(
            df=df_sheet,
            content_keyword_map=CONTENT_KEYWORD_MAP.get("balance_sheet"),
            threshold=80.0
        )
        if matched_content_key:
            print(f"Processing '{original_sheet_name}' mapped as '{matched_content_key}' via content fuzzy search")
            taraz[matched_content_key] = {"data": df_sheet}
        else:
            taraz[original_sheet_name] = {"data": df_sheet}

    imported.update(taraz)
    counts["ترازنامه"] = len(taraz)


    ca_path = file_paths.get("credit_approvals")
    if ca_path:
        try:
            tayidie = run_budget_extraction(
                budget_type="تاییدیه", excel_file_path={"تاییدیه": str(ca_path)},
                content_keyword_map=CONTENT_KEYWORD_MAP.get("credit_approvals")
            )
            imported.update(tayidie)
            counts["تاییدیه اعتبارات"] = len(tayidie)
        except Exception as exc:
            warnings.append(f"پردازش فایل «تاییدیه اعتبارات» با خطا مواجه شد: {exc}")

    bl_path = file_paths.get("budget_law")
    if bl_path:
        try:
            eblagh = run_budget_extraction(
                budget_type="ابلاغ", excel_file_path={"ابلاغ": str(bl_path)},
                content_keyword_map=CONTENT_KEYWORD_MAP.get("budget_law")
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


def _set_stage(job: Optional[dict[str, Any]], message: str) -> None:
    """در صورت وجود job (اجرای پس‌زمینه)، مرحله جاری و لاگ پردازش را به‌روزرسانی می‌کند."""
    if job is None:
        return
    job["stage"] = message
    job.setdefault("logs", []).append(message)


def run_checklist(
    imported_sheets: dict[str, Any],
    job: Optional[dict[str, Any]] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """اجرای تمام سوالات چک‌لیست و بازگرداندن (نتایج کامل، موارد عدم تطابق FALSE)."""
    questions = load_checklist_definitions()
    loaded_sheets = load_excels_to_ram(imported_sheets)
    #TODO: load_excels_to_ram changed in process

    from datetime import datetime
    d = datetime.now()
    ## year_relabeler excute on imported_sheets
    year_relabeler_result = analyze_dataframes(loaded_sheets["df"])
    print("relabeler:", year_relabeler_result["anchor"], year_relabeler_result["diagnostics"]["full_frequency"])
    relabed_loaded_sheets = {"df":apply_header_relabeling_to_many(loaded_sheets["df"], year_relabeler_result["replacement_map"])}
    #TODO: metadata complete and excuteable
    print("time relabel:", datetime.now()-d)


    results: list[dict[str, Any]] = []
    false_questions: list[dict[str, Any]] = []
    total_questions = len(questions)
    for q_index, question in enumerate(questions, start=1):
        q_id = question["question_id"]
        if q_index == 1 or q_index % 5 == 0 or q_index == total_questions:
            _set_stage(
                job,
                f"در حال اجرای چک‌لیست حسابرسی... (سوال {q_index} از {total_questions})",
            )
        evaluable = _has_evaluation_logic(question)

        record: dict[str, Any] = {
            "question_id": q_id,
            "question_text": question.get("question_text", ""),
            "question_purpose": question.get("question_porpose", ""),
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
                q_id, str(CHECKLIST_HANDLER_PATH), None, preloaded_sheets=relabed_loaded_sheets
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
        if record["status"] == "FALSE": 
            record.update({f"data_points_to_extract": question.get("data_points_to_extract")})
            false_questions.append(record)

    return results, false_questions


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
# تولید گزارش کمیسیون (موارد FALSE + متن اختیاری گزارش حسابرسی)
# ---------------------------------------------------------------------------

def generate_committee_report_output(
    false_questions: list[dict[str, Any]],
    workdir: Path,
    audit_report_path: Optional[Path] = None,
    entity_name: Optional[str] = None,
    job: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    # تولید گزارش کمیسیون بر اساس موارد FALSE چک‌لیست، به‌صورت اختیاری همراه با
    # متن گزارش حسابرسی بارگذاری‌شده توسط کاربر (اگر ارسال شده باشد).
    #
    # این تابع هیچ‌گاه خطایی را بالا پرتاب نمی‌کند: اگر تولید گزارش کمیسیون (به هر
    # دلیلی متن زمانی یا خطای LLM) شکست بخورد، نتیجه چک‌لیست حسابرسی همچنان باید
    # در داشبورد قابل نمایش بماند.
    from audit_report_generator.config import LLMConfig
    from audit_report_generator.report_generator import generate_committee_report
    from audit_report_generator.exceptions import AuditReportError
    from dotenv import load_dotenv

    doc_text: Optional[str] = None
    if audit_report_path is not None:
        _set_stage(job, "در حال استخراج متن از فایل گزارش حسابرسی بارگذاری‌شده...")
        try:
            extracted = extract_audit_report_text(str(audit_report_path))
            doc_text = extracted.text or None
        except (AuditReportExtractionError, FileNotFoundError) as exc:
            _set_stage(job, f"هشدار: استخراج متن گزارش حسابرسی ناموفق بود: {exc}")
            doc_text = None
        except Exception as exc:  # noqa: BLE001
            _set_stage(job, f"هشدار: خطای نامشخص هنگام استخراج متن گزارش حسابرسی: {exc}")
            doc_text = None

    if doc_text:
        _set_stage(
            job,
            "در حال تولید گزارش کمیسیون با هوش مصنوعی (با ترکیب موارد عدم تطابق چک‌لیست و متن گزارش حسابرسی)... این مرحله ممکن است چند دقیقه طول بکشد.",
        )
    else:
        _set_stage(
            job,
            "در حال تولید گزارش کمیسیون با هوش مصنوعی (بر مبنای موارد عدم تطابق چک‌لیست، بدون گزارش حسابرسی)... این مرحله ممکن است چند دقیقه طول بکشد.",
        )

    load_dotenv()
    api_key = os.getenv("API_KEY_OPENROUTER")
    if not api_key:
        raise ValueError("API_KEY_OPENROUTER در فایل env پیدا نشد!")
    print(f"کلید با موفقیت بارگذاری شد (فقط ۵ کاراکتر اول نشان داده می‌شود): {api_key[:5]}...")

    config = LLMConfig(
        api_key=os.environ.get("AUDIT_REPORT_LLM_API_KEY", api_key ),
        base_url=os.environ.get("AUDIT_REPORT_LLM_BASE_URL", "https://openrouter.ai/api/v1"),
        model=os.environ.get("AUDIT_REPORT_LLM_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free"),
        temperature=0.2,
    )

    output_path = workdir / "گزارش_کمیسیون.docx"
    try:
        generate_committee_report(
            checklist_json=false_questions,
            config=config,
            doc_text=doc_text,
            output_path=str(output_path),
            entity_name=entity_name,
        )
    except AuditReportError as exc:
        message = f"تولید گزارش کمیسیون ناموفق بود: {exc}"
        _set_stage(job, message)
        return {"error": message}
    except Exception as exc:  # noqa: BLE001
        message = f"خطای نامشخص هنگام تولید گزارش کمیسیون: {exc}"
        _set_stage(job, message)
        return {"error": message}

    _set_stage(job, "گزارش کمیسیون با موفقیت ساخته شد.")
    return {
        "docx_bytes": output_path.read_bytes(),
        "docx_filename": output_path.name,
        "used_audit_report_text": bool(doc_text),
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

def run_full_pipeline(
    file_paths: dict[str, Optional[Path]],
    workdir: Path,
    job: Optional[dict[str, Any]] = None,
    audit_report_path: Optional[Path] = None,
    entity_name: Optional[str] = None,
) -> dict[str, Any]:
    """
    اجرای کامل خط پردازش: استخراج/ادفام فایل‌های اکسل، اجرای چک‌لیست حسابرسی و
    درنهایت تولید گزارش کمیسیون (به صورت اختیاری، بر اساس موارد عدم تطابق چک‌لیست).

    اگر ``job`` داده شود، مرحله جاری فعلی در ``job["stage"]`` و لاگ کامل در
    ``job["logs"]`` نگه داشته می‌شود تا داشبورد بتواند به‌صورت زنده (تایمر + مرحله
    فعلی) وضعیت پردازش را نمایش دهد.
    """
    start = time.time()

    _set_stage(job, "در حال استخراج فایل‌های ورودی (بودجه، صورت‌های مالی، ترازنامه و ...)...")
    processed_inputs = build_imported_sheets(file_paths)
    for w in processed_inputs.warnings:
        _set_stage(job, f"هشدار: {w}")

    checklist_results, false_questions = run_checklist(processed_inputs.imported_sheets, job=job)
    summary = summarize_checklist(checklist_results)

    committee_report = generate_committee_report_output(
        false_questions,
        workdir=workdir,
        audit_report_path=audit_report_path,
        entity_name=entity_name,
        job=job,
    )

    _set_stage(job, "پردازش با موفقیت به اتمام رسید.")
    elapsed = time.time() - start
    return {
        "imported_sheets": processed_inputs.imported_sheets,
        "sheet_source_counts": processed_inputs.sheet_source_counts,
        "warnings": processed_inputs.warnings,
        "checklist_results": checklist_results,
        "false_questions": false_questions,
        "summary": summary,
        "committee_report": committee_report,
        "elapsed_seconds": elapsed,
    }


# ---------------------------------------------------------------------------
# اجرای پس‌زمینه (ترد مستقل) با وضعیت زنده برای داشبورد
# ---------------------------------------------------------------------------

def new_checklist_job() -> dict[str, Any]:
    """دیکشنری وضعیت اولیه برای پیگیری اجرای پس‌زمینه‌ی چک‌لیست حسابرسی."""
    return {
        "status": "idle",  # idle | running | done | error
        "result": None,
        "error": None,
        "thread": None,
        "started_at": None,
        "stage": "",
        "logs": [],
    }


def start_checklist_job(
    job: dict[str, Any],
    file_paths: dict[str, Optional[Path]],
    workdir: Path,
    audit_report_path: Optional[Path] = None,
    entity_name: Optional[str] = None,
) -> None:
    """
    اجرای run_full_pipeline در یک ترد پس‌زمینه‌ی جدا، دقیقاً مطابق الگوی
    start_audit_summary_job در audit_pipeline.py. ترد پس‌زمینه هرگز مستقیماً
    st.session_state[...] را تنظیم نمی‌کند، فقط فیلدهای درونی همین job dict را تقییر
    می‌دهد.
    """

    def _worker() -> None:
        job["status"] = "running"
        try:
            result = run_full_pipeline(
                file_paths,
                workdir=workdir,
                job=job,
                audit_report_path=audit_report_path,
                entity_name=entity_name,
            )
            job["result"] = result
            job["status"] = "done"
        except PipelineError as exc:
            job["error"] = str(exc)
            job["status"] = "error"
        except Exception as exc:  # noqa: BLE001
            job["error"] = f"خطای نامشخص در پردازش: {exc}"
            job.setdefault("logs", []).append(f"خطای نامشخص: {exc}")
            job["status"] = "error"
        finally:
            cleanup_workdir(workdir)

    job["status"] = "running"
    job["started_at"] = time.time()
    job["stage"] = "در حال شروع..."
    job["logs"] = []
    job["_ui_synced"] = False
    thread = threading.Thread(target=_worker, daemon=True)
    job["thread"] = thread
    thread.start()
