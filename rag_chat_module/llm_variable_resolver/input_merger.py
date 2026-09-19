"""
M1: merges the checklist spec with the operational results and
produces a flat list of VariableTask objects for variables that
still need resolution.

The "is this value missing?" decision is delegated to a
MissingValueStrategy so that rule can evolve without touching the
merge logic.
"""

from typing import Any, Dict, List, Optional

from .missing_value import DefaultMissingValueStrategy, MissingValueStrategy
from .models import VariableTask


class InputMerger:
    def __init__(self, missing_strategy: Optional[MissingValueStrategy] = None):
        self._missing = missing_strategy or DefaultMissingValueStrategy()

    def merge(
        self,
        checklist: List[Dict[str, Any]],
        operational: List[Dict[str, Any]],
    ) -> List[VariableTask]:
        operational_by_id = {e.get("question_id"): e for e in operational}
        tasks: List[VariableTask] = []

        for spec in checklist:
            question_id = spec.get("question_id")
            if not question_id:
                continue

            points = spec.get("data_points_to_extract") or []
            if not points:
                # No auto-resolvable spec (e.g. MANUAL questions).
                continue

            current_values = self._current_values(operational_by_id.get(question_id))

            for point in points:
                variable_name = point.get("variable_name")
                if not variable_name:
                    continue

                # Only variables whose current value is missing become tasks.
                if not self._missing.is_missing(current_values.get(variable_name)):
                    continue

                file_key = point.get("file") or spec.get("file")
                if not file_key:
                    # No way to know which file to open; skip rather than crash.
                    continue

                tasks.append(VariableTask(
                    question_id=question_id,
                    variable_name=variable_name,
                    file_key=file_key,
                    sheet_anchor=point.get("sheet_anchor") or "",
                    row_identifier=_join(point.get("row_identifier")),
                    column_identifier=_join(point.get("column_identifier")),
                    question_purpose=(
                        spec.get("question_purpose")
                        or spec.get("question_porpose")
                        or ""
                    ),
                    current_raw_value=current_values.get(variable_name),
                ))

        return tasks

    @staticmethod
    def _current_values(entry: Optional[Dict[str, Any]]) -> Dict[str, str]:
        if not entry:
            return {}
        values: Dict[str, str] = {}
        for row in entry.get("extracted_data") or []:
            name = row.get("متغیر")
            if name:
                values[name] = row.get("مقدار استخراج‌شده")
        return values


def _join(value: Any) -> str:
    """data_points sometimes carry lists (multi-row/multi-column specs).
    Flatten them into one identifier string using ' _ ' as the separator."""
    if value is None:
        return ""
    if isinstance(value, list):
        return " _ ".join(str(v) for v in value if v is not None)
    return str(value)


def build_variable_tasks(
    checklist: List[Dict[str, Any]],
    operational: List[Dict[str, Any]],
    missing_strategy: Optional[MissingValueStrategy] = None,
) -> List[VariableTask]:
    """Module-level convenience wrapper for InputMerger.merge()."""
    return InputMerger(missing_strategy).merge(checklist, operational)