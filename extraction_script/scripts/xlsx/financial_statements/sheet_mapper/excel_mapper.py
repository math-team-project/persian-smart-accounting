from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
_PATHS_TO_ADD = [
    ROOT_DIR,
    ROOT_DIR / "checklist",
]
for _p in _PATHS_TO_ADD:
    _p_str = str(_p)
    if _p_str not in sys.path:
        sys.path.insert(0, _p_str)

from normalize_persian import (  
    normalize_persian_text
)
import openpyxl
from typing import Dict, List, Any
from signatures import SIGNATURES
from sheet_classifier import classify_sheets_by_signature
import warnings
warnings.filterwarnings('ignore')  # Suppress all warnings


def process_excel_and_classify(file_path: str, similarity_threshold: float = 0.55) -> Dict[str, Any]:
    """
    Reads an Excel file, extracts row/col text layouts, normalizes Persian text,
    runs the signature-based classifier, and prints a structured mapping report.
    """
    # 1. Extract raw sheet skeletons directly from the Excel file
    sheets_skeleton = _extract_excel_skeletons(file_path)

    # 2. Run Task 2 classifier to map sheets to canonical signatures
    classification_results = classify_sheets_by_signature(sheets_skeleton, similarity_threshold=0.25)

    # 3. Format mapping summary report
    mapping_summary = []
    mapped_count = 0

    for res in classification_results:
        is_mapped = res["predicted_type"] != "unknown"
        if is_mapped:
            mapped_count += 1

        mapping_summary.append({
            "sheet_name": res["original_sheet_name"],
            "mapped_to_signature": res["predicted_type"],
            "confidence_score": res["confidence_score"],
            "description": res["signature_details"]
        })

    total_sheets = len(sheets_skeleton)
    
    output_report = {
        "summary": {
            "total_sheets": total_sheets,
            "successfully_mapped": mapped_count,
            "unmapped_unknown": total_sheets - mapped_count
        },
        "sheet_mappings": mapping_summary
    }

    # Print clean visual mapping to console
    _print_mapping_report(output_report)

    return output_report


def _extract_excel_skeletons(file_path: str) -> List[Dict[str, Any]]:
    """Helper to extract non-numeric text labels from Excel sheets."""
    workbook = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    sheets_skeleton = []

    for sheet_name in workbook.sheetnames:
        sheet = workbook[sheet_name]
        row_labels = set()
        col_labels = set()
        title_blocks = []

        for row_idx, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            row_text = []
            for cell in row:
                if cell is None:
                    continue
                
                cell_str = str(cell).strip()
                norm_text = normalize_persian_text(cell_str)
                if norm_text:
                    row_text.append(norm_text)

            if not row_text:
                continue

            if row_idx <= 5:
                title_blocks.extend(row_text)

            for text in row_text:
                if row_idx <= 3:
                    col_labels.add(text)
                else:
                    row_labels.add(text)

        sheets_skeleton.append({
            "sheet_name": sheet_name,
            "title_blocks": list(dict.fromkeys(title_blocks)),
            "row_labels": list(row_labels),
            "col_labels": list(col_labels)
        })

    workbook.close()
    return sheets_skeleton


def _print_mapping_report(report: Dict[str, Any]):
    """Helper function to print formatted results."""
    print("=" * 80)
    print("گزارش مپینگ شیت‌های اکسل به امضاهای کانونیکال (Signature Mapping)")
    print("=" * 80)
    print(f"تعداد کل شیت‌ها: {report['summary']['total_sheets']}")
    print(f"شیت‌های شناسایی‌شده: {report['summary']['successfully_mapped']}")
    print(f"شیت‌های ناشناخته (Unknown): {report['summary']['unmapped_unknown']}")
    print("-" * 80)
    print(f"{'عنوان شیت اکسل':<25} | {'امضای مپ شده (Signature)':<35} | {'امتیاز (Score)':<10}")
    print("-" * 80)

    for item in report["sheet_mappings"]:
        sheet_name = item["sheet_name"][:23]
        signature = item["mapped_to_signature"][:33]
        score = f"{item['confidence_score']:.2%}"
        print(f"{sheet_name:<25} | {signature:<35} | {score:<10}")

    print("=" * 80)

if __name__ == "__main__":
    file_path = "extraction_script//data//صورتهاي مالي 1398.xlsx"
    
    # فراخوانی تابع مپینگ
    result = process_excel_and_classify(file_path, similarity_threshold=0.20)