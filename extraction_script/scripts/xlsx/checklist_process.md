# Audit Checklist Processing Pipeline Documentation

## Overview
This Python script provides an automated, high-performance data extraction and evaluation pipeline for audit checklists. It is designed to process complex Excel and JSON audit files, leverage parallel computing for optimized performance, use advanced fuzzy text matching to retrieve data points, evaluate compliance conditions, and provide a unified entry point for both programmatic execution and command-line usage.

---

## Core Architecture & Components

### 1. Parallel Excel Loading & Normalization
* **Parallel Processing**: Utilizes `ProcessPoolExecutor` to load and normalize multiple Excel workbooks simultaneously into RAM, drastically reducing I/O bottlenecks.
* **Text & Sheet Normalization**: Standardizes Persian text layouts and dataframe structures upon ingestion using custom utility modules (`normalize_persian`).

### 2. Fuzzy Matching & Data Extraction (`find_value_in_sheet`)
* **Robust Search**: Uses `rapidfuzz` (`fuzz.WRatio` and `process.extract`) to accurately match row labels and column names, handling typos or minor discrepancies in audit documents.
* **Bracket Notation Support**: Automatically parses multi-value selection indices via square bracket annotations (e.g., matching a query like `جمع [1]`) to retrieve specific historical or indexed rows/columns.
* **Data Cleansing**: Automatically handles financial formats, stripping commas, extra whitespace, and converting accounting notations (e.g., negative values wrapped in parentheses like `(100)` into `-100`).

### 3. Logic & Condition Evaluator (`evaluate_logic`)
* **Syntax Translation**: Safely translates JavaScript-style operators (`&&`, `||`, `===`, `!==`) and Math functions (`Math.abs`, `Math.round`, `Math.max`, etc.) into valid Python expressions.
* **Sub-condition Breakdown**: Splits multi-variable condition strings into individual sub-conditions, evaluating each component independently while gracefully managing missing or `NaN` parameters.

### 4. Pipeline Execution & Summary Dashboard
* **Single Question Pipeline (`run_audit_pipeline`)**: Targets a specific audit question (`QID`), parses its JSON requirement, extracts variables, and evaluates compliance rules.
* **Batch Processing (`run_all_audit_questions`)**: Pre-loads files once and iterates through all checklist questions, generating a comprehensive summary dashboard (`summary_overview`) that tracks total counts, successes, failures, and execution errors.

---

## Unified Entry Point (`main`)
The script features a flexible `main()` function that adapts based on how it is invoked:
* **Programmatic Call**: Pass arguments directly from other Python modules or notebooks (e.g., running specific question IDs or batch processing with `"ALL"`).
* **Command-Line Interface (CLI)**: Automatically switches to `argparse` mode if parameters are omitted during direct terminal execution.

---

## Error Tracking & Status Categorization

The `summary_overview` dashboard includes robust error handling to distinguish between actual compliance failures and technical calculation errors:

* **Detection Mechanism**: The evaluation engine monitors sub-condition evaluations. If a variable is missing (`None`) or an expression triggers an exception during execution, it logs a `"FAILED"` status in the evaluation breakdown.
* **Error Classification**: Questions encountering internal evaluation exceptions or pipeline-level failures are automatically diverted from standard `False` results and tallied under `error_count`.
* **Traceability**: Failed or errored question IDs are isolated into specific lists (`false_question_ids` and `error_question_ids`), enabling developers to quickly debug missing dataset variables or broken formulas without crashing the entire batch run.
---

## Usage Guide

### 1. Command-Line Interface (CLI) Examples
Run all audit checklist questions from the terminal:
```bash
python checklist_process.py -q ALL -j path/to/handler.json -e path/to/budget.xlsx path/to/output.xlsx