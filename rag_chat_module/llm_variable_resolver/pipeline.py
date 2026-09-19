"""
M5: end-to-end pipeline.

Wires M1 (input merging) -> M2/M3 (file loading + size routing)
-> M4 (LLM extraction + redesign) and writes the results back into
the operational JSON.

The pipeline owns no resolution logic: which variables are missing
(M1), which slice to send (M3), how voters vote and whether to
redesign (M4) are all decided by the stage that already owns that
responsibility. This keeps each stage independently testable and
lets any stage be swapped without touching this file.
"""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd

from .content_locator import load_sheets, resolve_relevant_content
from .input_merger import build_variable_tasks
from .llm import (
    EnsembleExtractor,
    QuestionInfo,
    QuestionOrchestrator,
    QuestionResolution,
    RedesignEnsemble,
)
from .models import RelevantSlice, SheetData, VariableTask
from .size_router import SizeRouter

FileSource = Union[str, Path, pd.DataFrame, Dict[str, pd.DataFrame]]
FileRegistry = Dict[str, FileSource]
SheetDescriptions = Dict[str, Dict[str, str]]


class ResolverPipeline:
    """One-call facade over M1-M4."""

    def __init__(
        self,
        extractor: EnsembleExtractor,
        router: Optional[SizeRouter] = None,
        redesign_ensemble: Optional[RedesignEnsemble] = None,
    ):
        self._router = router or SizeRouter()
        self._orchestrator = QuestionOrchestrator(
            extractor=extractor,
            redesign_ensemble=redesign_ensemble,
        )

    def run(
        self,
        checklist: List[Dict[str, Any]],
        operational: List[Dict[str, Any]],
        file_registry: FileRegistry,
        sheet_descriptions: Optional[SheetDescriptions] = None,
    ) -> List[Dict[str, Any]]:
        """Return a new operational list with all resolvable questions filled."""
        tasks = build_variable_tasks(checklist, operational)
        by_question = _group_by_question(tasks)
        checklist_by_id = {q["question_id"]: q for q in checklist}

        results: Dict[str, QuestionResolution] = {}
        for question_id, q_tasks in by_question.items():
            spec = checklist_by_id.get(question_id)
            if spec is None:
                continue
            question = _to_question_info(spec)
        
            # Every variable declared by the question spec - not just the ones
            # we are about to extract. Variables already resolved by the caller's
            # own code must stay visible to the condition evaluator, otherwise
            # the condition fails with "Unknown variable" even though its value
            # is known.
            all_variable_names = {
                p.get("variable_name")
                for p in spec.get("data_points_to_extract", [])
                if p.get("variable_name")
            }
        
            slices = self._resolve_slices(q_tasks, file_registry, sheet_descriptions)
            sheets = self._load_sheets_for_redesign(q_tasks, file_registry)
            known_values = _known_values_from_operational(
                operational, question_id, all_variable_names
            )

            results[question_id] = self._orchestrator.resolve_question(
                question=question,
                tasks=q_tasks,
                slices=slices,
                known_values=known_values,
                sheets=sheets,
            )

        return _merge_into_operational(operational, results)

    def run_from_paths(
        self,
        checklist_path: Union[str, Path],
        operational_path: Union[str, Path],
        file_registry: FileRegistry,
        sheet_descriptions: Optional[SheetDescriptions] = None,
        output_path: Optional[Union[str, Path]] = None,
    ) -> List[Dict[str, Any]]:
        """Convenience wrapper for the common file-based case."""
        checklist = json.loads(Path(checklist_path).read_text(encoding="utf-8"))
        operational = json.loads(Path(operational_path).read_text(encoding="utf-8"))

        updated = self.run(checklist, operational, file_registry, sheet_descriptions)

        if output_path is not None:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_text(
                json.dumps(updated, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return updated

    # -- internals ------------------------------------------------------

    def _resolve_slices(
        self,
        tasks: List[VariableTask],
        file_registry: FileRegistry,
        sheet_descriptions: Optional[SheetDescriptions],
    ) -> Dict[str, RelevantSlice]:
        slices: Dict[str, RelevantSlice] = {}
        for task in tasks:
            slices[task.variable_name] = resolve_relevant_content(
                task=task,
                file_registry=file_registry,
                router=self._router,
                sheet_descriptions=sheet_descriptions,
            )
        return slices

    def _load_sheets_for_redesign(
        self,
        tasks: List[VariableTask],
        file_registry: FileRegistry,
    ) -> Optional[List[SheetData]]:
        seen: set = set()
        all_sheets: List[SheetData] = []
        for task in tasks:
            if task.file_key in seen:
                continue
            seen.add(task.file_key)
            source = file_registry.get(task.file_key)
            if source is None:
                continue
            all_sheets.extend(load_sheets(source))
        return all_sheets or None


# ---------------------------------------------------------------------------
# module-level helpers
# ---------------------------------------------------------------------------

def _group_by_question(tasks: List[VariableTask]) -> Dict[str, List[VariableTask]]:
    grouped: Dict[str, List[VariableTask]] = {}
    for task in tasks:
        grouped.setdefault(task.question_id, []).append(task)
    return grouped


def _to_question_info(spec: Dict[str, Any]) -> QuestionInfo:
    """Accept both shapes seen in real checklist files."""
    logic = spec.get("evaluation_logic") or {}
    condition = logic.get("condition") or spec.get("evaluation_condition") or ""
    return QuestionInfo(
        question_id=spec["question_id"],
        question_text=spec.get("question_text", ""),
        question_purpose=(
            spec.get("question_purpose")
            or spec.get("question_porpose", "")
        ),
        evaluation_condition=condition,
        original_data_points=spec.get("data_points_to_extract", []),
        on_true=logic.get("on_true", "") or "",
        on_false=logic.get("on_false", "") or "",
    )


def _known_values_from_operational(
    operational: List[Dict[str, Any]],
    question_id: str,
    variable_names: set,
) -> Dict[str, Optional[float]]:
    entry = next((e for e in operational if e.get("question_id") == question_id), None)
    if not entry:
        return {}

    values: Dict[str, Optional[float]] = {}
    for row in entry.get("extracted_data", []):
        name = row.get("متغیر")
        if name in variable_names:
            values[name] = _parse_numeric(row.get("مقدار استخراج‌شده"))
    return values


def _parse_numeric(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().replace(",", "").replace("،", "")
    if text in {"—", "-", "--", "None", "N/A"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _merge_into_operational(
    operational: List[Dict[str, Any]],
    results: Dict[str, QuestionResolution],
) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for entry in operational:
        new_entry = deepcopy(entry)
        result = results.get(entry.get("question_id"))
        if result is None:
            output.append(new_entry)
            continue

        new_entry["extracted_data"] = result.extracted_data
        new_entry["condition_breakdown"] = result.condition_breakdown
        new_entry["message"] = result.message
        new_entry["status"] = _status_to_operational(result.status)

        if result.redesign is not None and result.redesign.get("consensus"):
            new_entry["redesign_proposal"] = result.redesign["spec"]

        output.append(new_entry)
    return output


def _status_to_operational(status: str) -> str:
    return {
        "OK": "OK",
        "MISMATCH": "MISMATCH",
        "NEEDS_REVIEW": "ERROR",
        "REDESIGNED": "REDESIGNED",
    }.get(status, "ERROR")