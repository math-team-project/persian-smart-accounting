# Audit Checklist Processing Pipeline Documentation

## Overview
This Python script provides an automated, high-performance data extraction and evaluation pipeline for audit checklists[cite: 2]. It is designed to process complex Excel and JSON audit files, leverage parallel computing for optimized performance, and use advanced fuzzy text matching to retrieve data points[cite: 2]. The pipeline now natively supports extracting data from unstructured sheet metadata and converting evaluation string logic into LaTeX mathematical syntax[cite: 1, 3, 4]. It evaluates compliance conditions and provides a unified entry point for both programmatic execution and command-line usage[cite: 2].

---

## Core Architecture & Components

### 1. Parallel Excel Loading & Normalization
*   **Parallel Processing & RAM Loading**: Utilizes robust methods to load and normalize multiple Excel workbooks simultaneously into RAM, drastically reducing I/O bottlenecks.
*   **Dual Data Structures**: The loader (`load_excels_to_ram`) extracts both the standard dataframe structures (`df`) and unstructured sheet text/properties (`metadata`) for each sheet.
*   **Text & Sheet Normalization**: Standardizes Persian text layouts and dataframe structures upon ingestion using custom utility modules (`normalize_persian`).

### 2. Fuzzy Matching & Data Extraction
*   **Robust Row/Column Search**: Uses `rapidfuzz` (`fuzz.WRatio` and `process.extract`) to accurately match row labels and column names, handling typos or minor discrepancies in audit documents (`find_value_in_sheet`)[cite: 2].
*   **Fuzzy Sheet Matching**: If an exact string match for a sheet name fails, the `extract_data_points` function uses a fallback mechanism (`fuzz.partial_ratio`) to find the most accurate matched sheet in RAM[cite: 1].
*   **Unstructured Metadata Extraction**: If a targeted row identifier is flagged for metadata (e.g., begins with "توضیحات _"), it invokes the `search_in_text` function[cite: 1]. This tool utilizes token intersection and fuzzy matching to extract exact values (such as numeric amounts after the keyword "مبلغ") from loose text, ignoring structural changes[cite: 1, 4].
*   **Bracket Notation Support**: Automatically parses multi-value selection indices via square bracket annotations (e.g., matching a query like `جمع [1]`) to retrieve specific historical or indexed rows/columns[cite: 2].
*   **Data Cleansing**: Automatically handles financial formats, stripping commas, extra whitespace, and converting accounting notations (e.g., negative values wrapped in parentheses like `(100)` into `-100`)[cite: 2].

### 3. Logic & Condition Evaluator (`evaluate_logic`)
*   **Syntax Translation**: Safely translates JavaScript-style operators (`&&`, `||`, `===`, `!==`), Math functions (`Math.abs`, `Math.round`, `Math.max`, etc.), and string manipulation operations into valid Python expressions.
*   **Sub-condition Breakdown**: Splits multi-variable condition strings into individual sub-conditions, evaluating each component independently while gracefully managing missing or `NaN` parameters.
*   **Float Rounding Correction**: Pre-processes loaded variables inside the evaluation context by rounding numerical variables to `0` digits, minimizing false-negative float comparison errors.
*   **LaTeX Math Rendering**: Leverages an Abstract Syntax Tree (AST) parser (`math_to_latex`) to format string conditions into strictly rendered, nested LaTeX strings (returned as `evaluation_condition_latex` and `formatted_conditions_latex`) for accurate visual representation.

### 4. Pipeline Execution & Summary Dashboard
*   **Single Question Pipeline (`run_audit_pipeline`)**: Targets a specific audit question (`QID`), parses its JSON requirement, extracts variables, and evaluates compliance rules.
*   **Variable Hint Localization**: Before yielding results, the pipeline builds a hint mapping dictionary that dynamically converts underlying English variable names into their descriptive Persian `variable_hint` strings (`localized_extracted_vars`) for cleaner presentation on localized dashboards.
*   **Batch Processing (`run_all_audit_questions`)**: Pre-loads files once and iterates through all checklist questions, generating a comprehensive summary dashboard (`summary_overview`) that tracks total counts, successes, failures, and execution errors.

---

## Unified Entry Point (`main`)
The script features a flexible `main()` function that adapts based on how it is invoked:
*   **Programmatic Call**: Pass arguments directly from other Python modules or notebooks (e.g., running specific question IDs or batch processing with `"ALL"`).
*   **Command-Line Interface (CLI)**: Automatically switches to `argparse` mode if parameters are omitted during direct terminal execution[cite: 2].

---

## Error Tracking & Status Categorization

The `summary_overview` dashboard includes robust error handling to distinguish between actual compliance failures and technical calculation errors:

*   **Detection Mechanism**: The evaluation engine monitors sub-condition evaluations. If a variable is missing (`None`) or an expression triggers an exception during execution, it logs a `"FAILED"` status in the evaluation breakdown.
*   **Error Classification**: Questions encountering internal evaluation exceptions or pipeline-level failures are automatically diverted from standard `False` results and tallied under `error_count`.
*   **Traceability**: Failed or errored question IDs are isolated into specific lists (`false_question_ids` and `error_question_ids`), enabling developers to quickly debug missing dataset variables or broken formulas without crashing the entire batch run.

---

## Usage Guide

### 1. Command-Line Interface (CLI) Examples
Run all audit checklist questions from the terminal:
```bash
python checklist_process.py -q ALL -j path/to/handler.json -e path/to/budget.xlsx path/to/output.xlsx