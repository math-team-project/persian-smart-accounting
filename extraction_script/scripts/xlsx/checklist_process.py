from normalize_persian import normalize_persian_text, normalize_dataframe

import json
import numpy as np
import pandas as pd
import logging
import argparse
import math
import re
from rapidfuzz import fuzz, process
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _read_single_excel(file_path):
    sheets_dict = {}
    try:
        xls = pd.read_excel(file_path, sheet_name=None)
        for sheet_name, df in xls.items():
            norm_sheet_name = normalize_persian_text(sheet_name)
            sheets_dict[norm_sheet_name] = normalize_dataframe(df)
        logger.info(f"Successfully loaded and Normalized: {file_path}")
    except FileNotFoundError as e:
        logger.error(f"File not found: {file_path}")
    except Exception as e:
        logger.error(f"Error loading {file_path}: {e}")
    return sheets_dict


def load_excels_to_ram(file_paths):
    """
    Loads multiple Excel files into RAM as a dictionary of DataFrames.
    Normalizes sheet names and contents upon loading.
    """
    all_sheets = {}

    with ProcessPoolExecutor() as executor:
        results = executor.map(_read_single_excel, file_paths)
        for res in results:
            all_sheets.update(res)
    return all_sheets


# EXTRACTION LOGIC
# RapidFuzz
def find_value_in_sheet(df, row_id, col_id, cutoff=0.5):
    """
    Searches a dataframe using fuzzy matching and handles multi-value selection
    via square bracket indices specified in the identifier (e.g., 'جمع [1]').
    """
    try:
        row_query = str(row_id).strip()
        col_query = str(col_id).strip()

        # 1. Safely extract index from bracket notation (e.g., "جمع [2]") using regex
        target_index = None

        row_match = re.search(r'\[(\d+)\]$', row_query)
        if row_match:
            target_index = int(row_match.group(1))
            row_query = re.sub(r'\s*\[\d+\]$', '', row_query).strip()

        col_match = re.search(r'\[(\d+)\]$', col_query)
        if col_match and target_index is None:
            target_index = int(col_match.group(1))
            col_query = re.sub(r'\s*\[\d+\]$', '', col_query).strip()

        # 2. RapidFuzz match all matching columns
        col_names = [str(c) for c in df.columns]
        score_cutoff = cutoff * 100 if cutoff <= 1.0 else cutoff

        col_results = process.extract(col_query, col_names, scorer=fuzz.WRatio, limit=5, score_cutoff=score_cutoff)
        matched_cols = [item[0] for item in col_results]

        target_cols = matched_cols if matched_cols else []
        if not target_cols:
            for col in df.columns:
                sample_texts = [str(df.at[i, col]) for i in range(min(15, len(df))) if pd.notna(df.at[i, col])]
                sample_results = process.extract(col_query, sample_texts, scorer=fuzz.WRatio, limit=1, score_cutoff=score_cutoff)
                if sample_results:
                    target_cols.append(col)

        if not target_cols:
            logger.warning(f"No close column match found for '{col_query}'.")
            return None

        # 3. RapidFuzz match all matching rows
        row_text_to_indices = {}
        for idx, row in df.iterrows():
            for col in df.columns:
                val = row[col]
                if pd.notna(val):
                    text_val = str(val).strip()
                    if text_val not in row_text_to_indices:
                        row_text_to_indices[text_val] = []
                    row_text_to_indices[text_val].append(idx)

        all_row_texts = list(row_text_to_indices.keys())
        row_results = process.extract(row_query, all_row_texts, scorer=fuzz.WRatio, limit=3, score_cutoff=score_cutoff)
        matched_rows_text = [item[0] for item in row_results]

        if not matched_rows_text:
            logger.warning(f"No close row match found for '{row_query}'.")
            return None

        candidate_row_indices = []
        for r_text in matched_rows_text:
            candidate_row_indices.extend(row_text_to_indices[r_text])

        # 4. Extract all possible intersection values across matched rows and columns
        extracted_candidates = []
        for r_idx in candidate_row_indices:
            for c_col in target_cols:
                raw_val = df.at[r_idx, c_col]

                parsed_val = None
                if pd.notna(raw_val):
                    if isinstance(raw_val, str):
                        clean_val = raw_val.replace(",", "").replace(" ", "").strip()
                        if clean_val.startswith("(") and clean_val.endswith(")"):
                            clean_val = "-" + clean_val[1:-1]
                        try:
                            parsed_val = float(clean_val)
                        except ValueError:
                            parsed_val = None
                    else:
                        parsed_val = float(raw_val)

                extracted_candidates.append(parsed_val)

        numeric_candidates = [val for val in extracted_candidates if val is not None]

        if not numeric_candidates:
            return None

        # 5. Handle Selection Rules
        if target_index is not None:
            if target_index < len(numeric_candidates):
                return numeric_candidates[target_index]
            logger.warning(f"Index [{target_index}] out of range for {len(numeric_candidates)} numeric values found.")
            return None

        if len(numeric_candidates) == 1:
            return numeric_candidates[0]
        return numeric_candidates[0]

        # # 5. Handle Selection Rules:
        # if target_index is not None:
        #     # ۱. اگر می‌خواهید اندیس [i] دقیقاً از روی تمام مقادیر عددی پیدا شده انتخاب کند:
        #     numeric_candidates = [val for val in extracted_candidates if val is not None]
        #     if target_index < len(numeric_candidates):
        #         return numeric_candidates[target_index]

        #     # ۲. اگر می‌خواهید اندیس [i] از روی تمام مقادیر استخراج‌شده (حتی شامل None) انتخاب کند:
        #     # if target_index < len(extracted_candidates):
        #     #     return extracted_candidates[target_index]

        #     print(f"  -> Warning: Index [{target_index}] out of range for found values.")
        #     return None

    except Exception as e:
        logger.error(f"Error in fuzzy search for Row '{row_id}' / Col '{col_id}': {e}")
        return None


def extract_data_points(data_points, loaded_sheets):
    """
    Iterates through the question's data_points, maps them to the loaded sheets in RAM,
    and handles single string identifiers or lists of identifiers (like Q25).
    """
    extracted_data = {}

    for dp in data_points:
        var_name = dp.get("variable_name")
        sheet_anchor = normalize_persian_text(dp.get("sheet_anchor", ""))

        # Ensure row/col identifiers are lists to handle structures
        row_ids = dp.get("row_identifier")
        col_ids = dp.get("column_identifier")

        row_ids = [row_ids] if isinstance(row_ids, str) else row_ids
        col_ids = [col_ids] if isinstance(col_ids, str) else col_ids

        # Normalize identifiers
        row_ids = [normalize_persian_text(r) for r in row_ids] if row_ids else []
        col_ids = [normalize_persian_text(c) for c in col_ids] if col_ids else []

        # Find the correct sheet in RAM
        # Using fuzzy matching (in keyword) because sheet anchors might be substrings
        target_df = None
        for name, df in loaded_sheets.items():
            if sheet_anchor in name:
                target_df = df
                break

        extracted_values = []
        if target_df is not None:
            # Handle cases where multiple rows/cols are provided
            for r_id in row_ids:
                for c_id in col_ids:
                    val = find_value_in_sheet(target_df, r_id, c_id)
                    extracted_values.append(val)
        else:
            logger.warning(f"Sheet matching '{sheet_anchor}' not found in RAM.")
            extracted_values = [None]

        # If it's a single expected value, store the single float.
        # If it pulled multiple (because of lists in JSON), store the list to be handled by sum() in eval.
        if len(extracted_values) == 1:
            extracted_data[var_name] = extracted_values[0]
        else:
            extracted_data[var_name] = extracted_values
    return extracted_data


# MATH CONDITION EVALUATOR
def evaluate_logic(condition_str, variables, on_true, on_false):
    """
    Parses string conditions, splits them into individual sub-conditions,
    and evaluates each one using logging instead of print.
    """
    if not condition_str:
        return {"result": False, "message": "No condition provided.", "chained_result": False}

    # Define all transformation rules (mapping old patterns to new Python-compatible ones)
    replacements = [
        # Logical Operators
        ("&&", " and "),
        ("||", " or "),
        ("&", " and "),
        ("|", " or "),

        # Mathematical & Numerical Functions
        ("Math.abs", "abs"),
        ("Math.round", "round"),
        ("Math.floor", "floor"),
        ("Math.ceil", "ceil"),
        ("Math.min", "min"),
        ("Math.max", "max"),
        ("Math.pow", "pow"),
        ("Math.round", "round"),

        # String Case Operations
        (".toUpperCase()", ".upper()"),
        (".toLowerCase()", ".lower()"),
        (".trim()", ".strip()"),

        # Comparison and Equality
        ("===", "=="),
        ("!==", "!=")
    ]

    py_condition = condition_str
    for old, new in replacements:
        py_condition = py_condition.replace(old, new)

    # py_condition = re.sub(r'\b\d+\.\d+\b', lambda m: f"round({m.group(0)}, 2)", py_condition)
    sub_conditions = py_condition.split(" and ")

    logger.info("--- Evaluation Breakdown ---")
    all_true = True
    evaluated_results = []

    eval_context = {
        "abs": abs,
        # "round": lambda x, n=0: round(x, n),
        "round": round,
        "sum": sum,
        "min": min,
        "max": max,
        "pow": pow,
        "None": None,
        "NaN": float('nan'),
    }
    
    eval_context.update(variables)
    # before comparastion, numers round about 0 difits to fall floats. 
    eval_context = {k: (round(v, 0) if isinstance(v, (int, float)) and not math.isnan(v) else v) for k, v in eval_context.items()}    

    for sub_cond in sub_conditions:
        sub_cond_clean = sub_cond.strip()

        # Check if any variable present in this specific sub-condition is None or NaN
        has_missing_var = False
        for var_name, val in variables.items():
            # If the variable name appears in the sub-condition text and its value is None/NaN
            if var_name in sub_cond_clean:
                if val is None or (isinstance(val, float) and math.isnan(val)):
                    has_missing_var = True
                    break

        if has_missing_var:
            logger.info(f"Condition [{sub_cond_clean}]: Evaluated as False (contains None/NaN variable)")
            evaluated_results.append(False)
            all_true = False
            continue

        # Evaluate the sub-condition safely
        try:
            sub_result = bool(eval(sub_cond_clean, {"__builtins__": None}, eval_context))

            # چاپ وضعیت شرط با جایگذاری مقادیر در لاگ
            eval_logged_str = sub_cond_clean
            for v_name, v_val in variables.items():
                if v_name in eval_logged_str:
                    eval_logged_str = re.sub(rf'\b{re.escape(v_name)}\b', str(v_val), eval_logged_str)

            logger.info(f"Condition [({eval_logged_str})]: {sub_result}")
            evaluated_results.append(sub_result)
            if not sub_result:
                all_true = False
        except Exception as e:
                logger.error(f"Condition [{sub_cond_clean}]: FAILED TO EVALUATE ({e})")
                evaluated_results.append("FAILED")  # تغییر False به رشته FAILED برای ثبت خطا
                all_true = False

    logger.info("----------------------------")

    final_message = on_true if all_true else on_false

    return {
        "is_true": all_true,
        "message": final_message,
        "chained_result": all_true,
        "breakdown": evaluated_results
    }


# MAIN PIPELINE
def run_audit_pipeline(
    question_key, handler_json_path, excel_file_paths, preloaded_sheets=None
):
    """run all functions toghter and make a data pipline for extrxtion and resulting"""
    if not question_key.startswith("Q"):
        question_key = f"Q{question_key}"

    # json read
    try:
        with open(handler_json_path, "r", encoding="utf-8") as f:
            raw_data = f.read()
            questions = json.loads(raw_data).get("audit_questions", [])
    except Exception as e:
        logger.error(f"Failed to parse JSON: {e}")
        return None

    # 1. Find the question
    target_q = next(
        (q for q in questions if q.get("question_id") == question_key), None
    )
    if not target_q:
        logger.warning(f"Question {question_key} not found.")
        return None

    logger.info(f"Targeting: {question_key}")
    logger.info(f"Purpose: {target_q.get('question_porpose', 'N/A')}")

    # # 2. Preloaded Excels
    if preloaded_sheets is None:
        logger.info("Loading files to RAM and normalizing...")
        loaded_sheets = load_excels_to_ram(excel_file_paths)
    else:
        loaded_sheets = preloaded_sheets

    # 3. Extract data points
    data_points = target_q.get("data_points_to_extract", [])
    extracted_vars = extract_data_points(data_points, loaded_sheets)

    logger.debug(
        f"Extracted Variables:\n{json.dumps(extracted_vars, indent=4, ensure_ascii=False)}"
    )

    # 4. Evaluate
    eval_data = target_q.get("evaluation_logic", {})
    condition_str = eval_data.get("condition") or eval_data.get("formula")

    result = evaluate_logic(
        condition_str,
        extracted_vars,
        eval_data.get("on_true", "True"),
        eval_data.get("on_false", "False"),
    )

    logger.info(f"FINAL RESULT ({question_key}): {result.get('is_true')} -> {result.get('message')}")

    return {
        "question_id": question_key,
        "extracted_data": extracted_vars,
        "evaluation_result": result,
    }


def run_all_audit_questions(handler_json_path, excel_file_paths):
    """اجرای تمامی سوالات موجود در فایل JSON و ارائه نمایه کلی از وضعیت آن‌ها."""
    try:
        with open(handler_json_path, "r", encoding="utf-8") as f:
            questions = json.loads(f.read()).get("audit_questions", [])
    except Exception as e:
        logger.error(f"Failed to read JSON for batch execution: {e}")
        return None

    # preloading
    logger.info("Pre-loading Excel files into RAM for batch question processing...")
    loaded_sheets = load_excels_to_ram(excel_file_paths)

    # Summary Dashboard
    results_summary = {
        "summary_overview": {
            "total_questions": len(questions),
            "true_count": 0,
            "false_count": 0,
            "error_count": 0,
            "true_question_ids": [],
            "false_question_ids": [],
            "error_question_ids": []
        },
        "details": []
    }

    for q in questions:
        q_id = q.get("question_id")
        try:
            res = run_audit_pipeline(
                q_id, handler_json_path, excel_file_paths, preloaded_sheets=loaded_sheets
            )
            
            if res is None or "evaluation_result" not in res:
                raise ValueError("Pipeline execution returned empty or invalid structure.")

            eval_res = res.get("evaluation_result", {})
            is_true = eval_res.get("is_true", False)
            breakdown = eval_res.get("breakdown", [])

            # Check if any evaluation breakdown failed due to an exception/error string
            has_eval_error = any(b == "FAILED" or b is None for b in breakdown)

            if has_eval_error:
                # Count as error if evaluation failed internally (e.g. NoneType subscriptable)
                results_summary["summary_overview"]["error_count"] += 1
                results_summary["summary_overview"]["error_question_ids"].append(q_id)
            elif is_true:
                results_summary["summary_overview"]["true_count"] += 1
                results_summary["summary_overview"]["true_question_ids"].append(q_id)
            else:
                results_summary["summary_overview"]["false_count"] += 1
                results_summary["summary_overview"]["false_question_ids"].append(q_id)

            # results_summary["details"].append(res)

        except Exception as e:
            # Catch unexpected pipeline-level exceptions
            logger.error(f"Error processing question {q_id}: {e}")
            results_summary["summary_overview"]["error_count"] += 1
            results_summary["summary_overview"]["error_question_ids"].append(q_id)

    overview = results_summary["summary_overview"]
    logger.info(
        f"Batch Run Finished -> Total: {overview['total_questions']} | "
        f"True: {overview['true_count']} | False: {overview['false_count']} | "
        f"Errors: {overview['error_count']}"
    )
    return results_summary


def main(questions=None, json_path=None, excel_paths=None):
    """
    Unified entry point for both CLI (Terminal) and direct Python invocation.

    :param questions: Question ID(s) as a string, list, or 'ALL'/None for all questions.
    :param json_path: Path to the JSON handler file.
    :param excel_paths: List of Excel file paths or a single file path string.
    :return: Dictionary or list containing evaluation results.
    """
    # 1. Parse arguments from CLI if parameters are not provided via Python call
    if json_path is None or excel_paths is None:
        parser = argparse.ArgumentParser(
            description="Audit Checklist Processing Pipeline"
        )
        parser.add_argument(
            "-q",
            "--questions",
            nargs="+",
            help="Question ID(s) to process (e.g., '1 2 3' or 'Q1 Q2'). Leave empty or pass 'ALL' to run all.",
            default=None,
        )
        parser.add_argument(
            "-j",
            "--json",
            required=True,
            help="Path to the JSON questions handler file.",
        )
        parser.add_argument(
            "-e",
            "--excels",
            nargs="+",
            required=True,
            help="List of path(s) to Excel process files.",
        )

        args = parser.parse_args()
        questions = args.questions if questions is None else questions
        json_path = args.json if json_path is None else json_path
        excel_paths = args.excels if excel_paths is None else excel_paths

    # 2. Standardize input types
    if isinstance(excel_paths, str):
        excel_paths = [excel_paths]

    if isinstance(questions, str):
        questions = [questions]

    # 3. Execution Logic
    # Case A: Process all questions
    if not questions or "ALL" in [str(q).upper() for q in questions]:
        final_output = run_all_audit_questions(json_path, excel_paths)

    # Case B: Process a single question
    elif len(questions) == 1:
        final_output = run_audit_pipeline(questions[0], json_path, excel_paths)

    # Case C: Process multiple specific questions with pre-loaded Excel sheets
    else:
        logger.info("Pre-loading Excel files into RAM for selected questions...")
        loaded_sheets = load_excels_to_ram(excel_paths)
        final_output = []
        for q_id in questions:
            res = run_audit_pipeline(
                q_id, json_path, excel_paths, preloaded_sheets=loaded_sheets
            )
            final_output.append(res)

    return final_output


if __name__ == "__main__":
    ## CLI: uv run .\extraction_script\scripts\xlsx\checklist_process.py -q ALL -j .\extraction_script\data\Checklist_Question_extracted_handler.json -e .\extraction_script\data\check_budget.xlsx .\extraction_script\data\check_output.xlsx 'extraction_script\data\????.xlsx' 'extraction_script\data\?????.xlsx' 'extraction_script\data\???????.xlsx'
    main()

    #Argument Base
    # from config import CHECKLIST_PROCESS_EXCEL_PATH, CHECKLIST_EXTRACTED_SAMPLE, CHECKLIST_EXTRACTED_HANDLER
    # main("ALL", CHECKLIST_EXTRACTED_HANDLER, CHECKLIST_PROCESS_EXCEL_PATH)
