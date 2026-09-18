from pathlib import Path
import sys
import re
from typing import Dict, List, Any, Tuple
from signatures import SIGNATURES

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


def classify_sheets_by_signature(sheets_data: List[Dict[str, Any]], similarity_threshold: float = 0.25) -> List[Dict[str, Any]]:
    """
    Task 2: Classifies Excel sheets based on dynamic header/content matching.
    Ignores sheet names and dynamically incorporates all signature elements (title, core/common rows/cols, keywords).
    """
    classified_results = []
    normalized_signatures = _get_normalized_signatures()

    for sheet in sheets_data:
        raw_rows = sheet.get("row_labels", []) + sheet.get("title_blocks", [])
        raw_cols = sheet.get("col_labels", [])

        # Normalize sheet content
        sheet_rows_norm = set(normalize_persian_text(str(r)) for r in raw_rows if r)
        sheet_cols_norm = set(normalize_persian_text(str(c)) for c in raw_cols if c)
        all_sheet_text = " ".join(sheet_rows_norm | sheet_cols_norm)

        best_match_key = "unknown"
        best_score = 0.0

        for sig_key, sig_data in normalized_signatures.items():
            score = _calculate_dynamic_signature_score(
                sheet_rows=sheet_rows_norm,
                sheet_cols=sheet_cols_norm,
                all_sheet_text=all_sheet_text,
                signature=sig_data
            )

            if score > best_score:
                best_score = score
                best_match_key = sig_key

        final_type = best_match_key if best_score >= similarity_threshold else "unknown"

        classified_results.append({
            "original_sheet_name": sheet.get("sheet_name", ""),
            "predicted_type": final_type,
            "confidence_score": round(best_score, 4),
            "signature_details": SIGNATURES.get(final_type, {}).get("comment", "شناسایی نشده")
        })

    return classified_results


def _calculate_dynamic_signature_score(sheet_rows: set, sheet_cols: set, all_sheet_text: str, signature: dict) -> float:
    """
    Calculates weighted score dynamically based on available keys in each signature.
    Base Weights (if key exists):
      - core_rows:   0.40
      - title:       0.20
      - core_cols:   0.20
      - keywords:    0.10
      - common_cols: 0.05
      - common_rows: 0.05
    """
    base_weights = {
        "core_rows": 0.25,
        "title": 0.30,
        "core_cols": 0.20,
        "keywords": 0.05,
        "common_cols": 0.05,
        "common_rows": 0.05
    }

    component_scores = {}
    active_weights_sum = 0.0

    # 1. Check Title (if present in signature)
    if "title" in signature and signature["title"]:
        title_text = signature["title"]
        component_scores["title"] = 1.0 if title_text in all_sheet_text else 0.0
        active_weights_sum += base_weights["title"]

    # 2. Check Core Rows (if present in signature)
    if "core_rows" in signature and signature["core_rows"]:
        matched = sum(1 for item in signature["core_rows"] if _contains_fuzzy(item, sheet_rows))
        component_scores["core_rows"] = matched / len(signature["core_rows"])
        active_weights_sum += base_weights["core_rows"]

    # 3. Check Core Cols (if present in signature)
    if "core_cols" in signature and signature["core_cols"]:
        matched = sum(1 for item in signature["core_cols"] if _contains_fuzzy(item, sheet_cols))
        component_scores["core_cols"] = matched / len(signature["core_cols"])
        active_weights_sum += base_weights["core_cols"]

    # 4. Check Keywords (if present in signature)
    if "keywords" in signature and signature["keywords"]:
        matched = sum(1 for kw in signature["keywords"] if kw in all_sheet_text)
        component_scores["keywords"] = matched / len(signature["keywords"])
        active_weights_sum += base_weights["keywords"]

    # 5. Check Common Cols (if present in signature)
    if "common_cols" in signature and signature["common_cols"]:
        matched = sum(1 for item in signature["common_cols"] if _contains_fuzzy(item, sheet_cols))
        component_scores["common_cols"] = matched / len(signature["common_cols"])
        active_weights_sum += base_weights["common_cols"]

    # 6. Check Common Rows (if present in signature)
    if "common_rows" in signature and signature["common_rows"]:
        matched = sum(1 for item in signature["common_rows"] if _contains_fuzzy(item, sheet_rows))
        component_scores["common_rows"] = matched / len(signature["common_rows"])
        active_weights_sum += base_weights["common_rows"]

    if active_weights_sum == 0:
        return 0.0

    # Dynamic Normalization of weights so the total sum always equals 1.0
    final_score = sum(
        component_scores[key] * (base_weights[key] / active_weights_sum)
        for key in component_scores
    )

    return final_score


def _contains_fuzzy(target_pattern: str, text_set: set) -> bool:
    """Checks if target canonical label exists within any extracted text label."""
    for text in text_set:
        if target_pattern in text or text in target_pattern:
            return True
    return False


def _get_normalized_signatures() -> Dict[str, Dict[str, Any]]:
    """Normalizes all string elements in SIGNATURES catalog dynamically."""
    normalized = {}
    for key, data in SIGNATURES.items():
        sig_dict = {}
        if "title" in data:
            sig_dict["title"] = normalize_persian_text(data["title"])
        
        for list_key in ["core_rows", "common_rows", "core_cols", "common_cols", "keywords"]:
            if list_key in data:
                sig_dict[list_key] = [normalize_persian_text(x) for x in data[list_key] if x]
                
        normalized[key] = sig_dict
    return normalized