"""
Prompt builders for the two LLM jobs in M4.

Kept as small classes (not bare functions) so a Voter can plug a
different prompt variant without touching the extractor or the
orchestrator - matching the user's requirement to allow N independent
LLMs with possibly different wordings.
"""

import json
from typing import Any, Dict, List, Tuple, Optional, Callable

from ..models import RelevantSlice, VariableTask

# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

_EXTRACTION_SYSTEM = """\
You are a meticulous financial-data extraction assistant working on
Persian financial statements and audit checklists.

Your single job: given (a) a checklist variable's identifying fields
and (b) one or more tables extracted from a financial file, locate the
exact numeric value that the variable refers to.

Hard rules:
1. Reply with ONE JSON object and nothing else - no prose, no markdown
   fences, no commentary before or after.
2. If the value cannot be determined with confidence from the given
   tables, return "value": null and "confidence": "needs_review".
   Never guess or interpolate.
3. Numbers must be plain (no commas, no currency symbols, no thousand
   separators, no Persian/Arabic digits). Convert Persian digits to
   ASCII before returning.
4. The row_identifier / column_identifier fields may be partial, or
   may combine several rows/columns of the table - read the table
   semantically, not only by literal substring.
5. The sheet_anchor may match a sheet name, a sheet description, or
   merely describe the section that contains the value.

Output schema (exactly these keys):
{
  "value": <number|null>,
  "confidence": "high" | "medium" | "needs_review",
  "matched_row": "<the actual row label in the table you used, or empty>",
  "matched_column": "<the actual column header, or empty>",
  "source_sheet": "<the sheet name where the value was found, or empty>",
  "reasoning": "<one short sentence explaining how you located the value>"
}"""


class ExtractionPromptBuilder:
    """Builds the (system, user) pair for single-variable extraction."""
    def __init__(self, normalize_fn: Optional[Callable[[str], str]] = None):
        self._normalize = normalize_fn or (lambda x: x or "")
            
    def build(self, task: VariableTask, relevant_slice: RelevantSlice) -> Tuple[str, str]:
        anchor = self._normalize(task.sheet_anchor)
        row_id = self._normalize(task.row_identifier)
        col_id = self._normalize(task.column_identifier)

        user = f"""\
Target variable: {task.variable_name}

Question purpose (context only - helps you understand what the value
represents):
{task.question_purpose}

Checklist identifiers for this variable:
- sheet_anchor:       {task.sheet_anchor}
- row_identifier:     {task.row_identifier}
- column_identifier:  {task.column_identifier}

Candidate table(s) extracted for this variable:
{relevant_slice.content}

Return the JSON object now."""
        return _EXTRACTION_SYSTEM, user


# ---------------------------------------------------------------------------
# Question redesign (only used on persistent mismatch)
# ---------------------------------------------------------------------------

_REDESIGN_SYSTEM = """\
You are a senior financial-audit assistant.

A checklist question about a Persian financial file did not evaluate
as expected even after its variable values were extracted. Your job is
to decide whether the question's data-point specification matches the
file's actual structure, and if not, to propose a corrected one.

You do NOT have access to the numeric content of the sheets - only a
short structural template of each sheet (its name and a sample of its
text labels). Use that to sanity-check the requested sheet_anchor /
row_identifier / column_identifier, and to propose better ones when
the current spec clearly cannot exist in the file.

Hard rules:
1. Reply with ONE JSON object and nothing else.
2. If the original specification is plausible and no clearly better
   option exists in the templates, keep it unchanged and set
   "is_evaluable_with_current_context": true.
3. If the spec is clearly wrong (e.g. the requested sheet does not
   appear in the templates, or the requested labels are nowhere to be
   seen), propose a corrected spec that uses labels you can actually
   see in the templates, and set
   "is_evaluable_with_current_context": false.
4. evaluation_logic.condition must use ONLY variable names from
   data_points_to_extract, and ONLY these operators/functions:
     + - * / % **  == != < <= > >=  and or not
     round() abs() min() max()

Output schema:
{
  "is_evaluable_with_current_context": true|false,
  "data_points_to_extract": [
    {"variable_name": "...", "sheet_anchor": "...",
     "row_identifier": "...", "column_identifier": "..."}
  ],
  "evaluation_logic": {
    "condition": "<python-like boolean expression>",
    "on_false": "<Persian sentence describing what a False result means>",
    "on_true":  "<Persian sentence describing what a True result means>"
  },
  "reasoning": "<one short paragraph>"
}"""


class RedesignPromptBuilder:
    """Builds the (system, user) pair for question-structure redesign."""
    def __init__(self, normalize_fn: Optional[Callable[[str], str]] = None):
            self._normalize = normalize_fn or (lambda x: x or "")
            
    def build(
        self,
        question_text: str,
        question_purpose: str,
        original_data_points: List[Dict[str, Any]],
        extracted_values: Dict[str, Any],
        condition_breakdown: List[Dict[str, str]],
        sheet_templates: str,
        full_sheet_content: str = "",
    ) -> Tuple[str, str]:

        normalized_points = [
            {**p,
                "sheet_anchor": self._normalize(p.get("sheet_anchor", "")),
                "row_identifier": self._normalize(p.get("row_identifier", "")),
                "column_identifier": self._normalize(p.get("column_identifier", ""))}
            for p in original_data_points
        ]

        extra = ""
        if full_sheet_content:
            extra = f"""\
        
                    Full content of the most relevant sheets (retrieved via vector search):
                    {full_sheet_content}
                    
                    When the templates above are not enough to decide, use the full content
                    below to (a) identify the correct rows/columns, or (b) rebuild the
                    question's data-point specification from scratch if the original one
                    was based on labels that do not exist.
                    """
        user = f"""\
                Original question:
                {question_text}
                
                Question purpose:
                {question_purpose}
                
                Original data-point specification:
                {json.dumps(normalized_points, ensure_ascii=False, indent=2)}
                
                Values already extracted by the extraction stage:
                {json.dumps(extracted_values, ensure_ascii=False, indent=2)}
                
                Re-evaluation result (why the condition did not pass):
                {json.dumps(condition_breakdown, ensure_ascii=False, indent=2)}
                
                Structural templates of every sheet in the file:
                {sheet_templates}{extra}
                
                You may either:
                A) Confirm the current specification is right - set
                    "is_evaluable_with_current_context": true.
                B) Correct the specification to use labels you can actually see.
                C) Rebuild the question completely from scratch if the original one
                    appears mismatched to the file's structure.
                
                Return the JSON object now."""
        return _REDESIGN_SYSTEM, user