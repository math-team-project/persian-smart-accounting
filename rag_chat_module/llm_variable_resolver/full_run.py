"""
Full audit run orchestrator.

Walks the whole pipeline (M1 -> M5) for a batch of questions and
produces a detailed, JSON-serializable diagnostic report alongside the
updated operational JSON.

Design goals:
  * Visibility - every stage (file loading, indexing, slice resolution,
    extraction, evaluation, redesign) is recorded per variable, so a
    failed run tells you exactly where it broke and why.
  * Non-destructive - the pipeline remains intact; this module only
    wires the existing pieces together and adds reporting.
  * Reusable - one public function `run_full_audit(...)` for callers
    who just want the report + updated operational JSON.
"""

from __future__ import annotations

import logging
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from .content_locator import load_sheets, resolve_relevant_content
from .condition_evaluator import MathConditionEvaluator
from .input_merger import build_variable_tasks
from .models import RelevantSlice, SheetData, VariableTask
from .retrieval import ChunkRetriever
from .size_router import SizeRouter

logger = logging.getLogger(__name__)


# ===========================================================================
# Report shapes
# ===========================================================================

@dataclass
class StageReport:
    """One action inside the pipeline for a specific variable/question."""
    stage: str                      # e.g. "load_file", "index_file", "resolve_slice"
    status: str                     # "ok" | "skipped" | "error"
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VariableReport:
    """Everything that happened for a single variable of a question."""
    question_id: str
    variable_name: str
    file_key: str
    sheet_anchor: str = ""
    row_identifier: str = ""
    column_identifier: str = ""

    # slice resolution
    slice_method: Optional[str] = None            # "slice_prompt" | "full_file_prompt" | "chunk_retrieval"
    slice_ambiguous: Optional[bool] = None
    slice_matched_sheets: List[str] = field(default_factory=list)
    slice_size: int = 0

    # extraction
    extracted_value: Optional[float] = None
    extraction_confidence: Optional[str] = None   # "high" | "medium" | "needs_review"
    extraction_consensus: Optional[bool] = None
    per_voter: List[Dict[str, Any]] = field(default_factory=list)

    # diagnostics
    stages: List[StageReport] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QuestionReport:
    """Full report for one checklist question."""
    question_id: str
    question_text: str = ""
    tasks_built: int = 0
    variables: List[VariableReport] = field(default_factory=list)

    condition_passed: Optional[bool] = None
    condition_status: Optional[str] = None        # "TRUE" | "FALSE" | "ERROR"
    condition_breakdown: List[Dict[str, Any]] = field(default_factory=list)

    status: str = "PENDING"                       # OK | MISMATCH | REDESIGNED | ERROR | MANUAL
    message: str = ""
    redesign_proposal: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["variables"] = [v.to_dict() for v in self.variables]
        return payload


@dataclass
class FullRunReport:
    """Top-level report for a complete audit run."""
    started_at: str
    finished_at: str = ""
    files_loaded: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    files_indexed: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    questions: List[QuestionReport] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    updated_operational: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "files_loaded": self.files_loaded,
            "files_indexed": self.files_indexed,
            "questions": [q.to_dict() for q in self.questions],
            "summary": self.summary,
            "updated_operational": self.updated_operational,
        }


# ===========================================================================
# Orchestrator
# ===========================================================================

class FullRunOrchestrator:
    """Walk every stage and record a per-variable, per-question trace."""

    def __init__(
        self,
        extractor,
        router: Optional[SizeRouter] = None,
        redesign_ensemble=None,
        evaluator=None,
        knowledge_base: Optional[KnowledgeBase] = None,   # ← NEW
    ):
        self._extractor = extractor
        self._router = router or SizeRouter()
        self._redesign = redesign_ensemble
        self._evaluator = evaluator or MathConditionEvaluator()
        self._kb = knowledge_base
    # ---------------------------------------------------------------------
    # public entry point
    # ---------------------------------------------------------------------

    def run(
        self,
        checklist: List[Dict[str, Any]],
        operational: List[Dict[str, Any]],
        file_registry: Dict[str, Any],
        sheet_descriptions: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> FullRunReport:
        report = FullRunReport(
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        # --- stage 1: build tasks (M1) -------------------------------
        try:
            tasks = build_variable_tasks(checklist, operational)
        except Exception as exc:
            report.summary = {"stage": "input_merger", "error": str(exc)}
            report.finished_at = datetime.now(timezone.utc).isoformat()
            return report

        tasks_by_question = _group_by_question(tasks)
        checklist_by_id = {q["question_id"]: q for q in checklist}

        # --- stage 2: load every file referenced by any task (M2) ----
        needed_file_keys = _collect_file_keys(tasks, file_registry)
        loaded_sheets: Dict[str, List[SheetData]] = {}
        for file_key in needed_file_keys:
            self._load_one_file(
                file_key=file_key,
                file_registry=file_registry,
                loaded_sheets=loaded_sheets,
                report=report,
            )

        # --- stage 3: index each loaded file into the vector store (M3) ---
        self._index_files(loaded_sheets, report)

        # --- stage 4: per question ---------------------------------
        for question_id, q_tasks in tasks_by_question.items():
            spec = checklist_by_id.get(question_id)
            if spec is None:
                continue
            q_report = self._run_question(
                spec=spec,
                tasks=q_tasks,
                operational=operational,        # ← NEW
                file_registry=file_registry,
                sheet_descriptions=sheet_descriptions,
                loaded_sheets=loaded_sheets,
            )
            report.questions.append(q_report)
            
        # --- stage 5: build updated operational --------------------
        report.updated_operational = _merge_operational(
            operational, {q.question_id: q for q in report.questions},
        )

        # --- stage 6: summarize ------------------------------------
        report.summary = _summarize(report)
        report.finished_at = datetime.now(timezone.utc).isoformat()
        return report

    # ---------------------------------------------------------------------
    # stage 2 - file loading
    # ---------------------------------------------------------------------

    def _load_one_file(
        self,
        file_key: str,
        file_registry: Dict[str, Any],
        loaded_sheets: Dict[str, List[SheetData]],
        report: FullRunReport,
    ) -> None:
        source = file_registry.get(file_key)
        if source is None:
            report.files_loaded[file_key] = {
                "status": "error",
                "error": f"file_key not present in registry",
            }
            return
        try:
            sheets = load_sheets(source)
            loaded_sheets[file_key] = sheets
            report.files_loaded[file_key] = {
                "status": "ok",
                "sheet_count": len(sheets),
                "sheets": [
                    {"name": s.name, "rows": int(s.dataframe.shape[0]),
                     "cols": int(s.dataframe.shape[1])}
                    for s in sheets[:30]
                ],
            }
        except Exception as exc:
            report.files_loaded[file_key] = {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }

    # ---------------------------------------------------------------------
    # stage 3 - indexing
    # ---------------------------------------------------------------------

    def _index_files(
        self,
        loaded_sheets: Dict[str, List[SheetData]],
        report: FullRunReport,
    ) -> None:
        retriever: ChunkRetriever = getattr(self._router, "_chunk_retriever", None)
        if retriever is None:
            report.files_indexed["*"] = {
                "status": "skipped",
                "reason": "router has no chunk retriever",
            }
            return

        for file_key, sheets in loaded_sheets.items():
            try:
                retriever.index_file(file_key, sheets)
                store = retriever._stores.get(file_key)
                report.files_indexed[file_key] = {
                    "status": "ok",
                    "chunks": len(getattr(store, "_chunks", [])) or None,
                }
            except Exception as exc:
                report.files_indexed[file_key] = {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                }

    # ---------------------------------------------------------------------
    # stage 4 - one question
    # ---------------------------------------------------------------------

    def _run_question(
        self,
        spec: Dict[str, Any],
        tasks: List[VariableTask],
        operational: List[Dict[str, Any]],   
        file_registry: Dict[str, Any],
        sheet_descriptions: Optional[Dict[str, Dict[str, str]]],
        loaded_sheets: Dict[str, List[SheetData]],
    ) -> QuestionReport:
        question_id = spec["question_id"]
        q = QuestionReport(
            question_id=question_id,
            question_text=spec.get("question_text", ""),
            tasks_built=len(tasks),
        )

        # Every variable the question declares - not just the ones we are
        # about to extract. Values already resolved by the caller's own
        # code must stay visible to the condition evaluator.
        all_variable_names = [
            p.get("variable_name")
            for p in spec.get("data_points_to_extract", [])
            if p.get("variable_name")
        ]
        
        # collect slices + per-variable reports
        slices: Dict[str, RelevantSlice] = {}
        for task in tasks:
            var_report = VariableReport(
                question_id=question_id,
                variable_name=task.variable_name,
                file_key=task.file_key,
                sheet_anchor=task.sheet_anchor,
                row_identifier=task.row_identifier,
                column_identifier=task.column_identifier,
            )

            if task.file_key not in file_registry:
                var_report.error = f"file_key '{task.file_key}' not in registry"
                var_report.stages.append(StageReport(
                    stage="resolve_slice", status="error",
                    message=var_report.error,
                ))
                q.variables.append(var_report)
                continue

            try:
                sl = resolve_relevant_content(
                    task=task,
                    file_registry=file_registry,
                    router=self._router,
                    sheet_descriptions=sheet_descriptions,
                )
                slices[task.variable_name] = sl

                var_report.slice_method = sl.resolution_method
                var_report.slice_ambiguous = sl.is_ambiguous
                var_report.slice_matched_sheets = list(sl.matched_sheet_names)
                var_report.slice_size = sl.size_estimate

                var_report.stages.append(StageReport(
                    stage="resolve_slice", status="ok",
                    message=f"method={sl.resolution_method}, ambiguous={sl.is_ambiguous}",
                    data={"matched_sheets": sl.matched_sheet_names,
                          "size": sl.size_estimate},
                ))
            except Exception as exc:
                var_report.error = f"{type(exc).__name__}: {exc}"
                var_report.stages.append(StageReport(
                    stage="resolve_slice", status="error",
                    message=var_report.error,
                    data={"traceback": traceback.format_exc()},
                ))
            q.variables.append(var_report)

        # extraction via ensemble
        values: Dict[str, Optional[float]] = _known_values_for_question(
               operational, question_id, set(all_variable_names),
           )
        for task in tasks:
            vr = _find_var_report(q.variables, task.variable_name)
            sl = slices.get(task.variable_name)
            if sl is None:
                continue
            try:
                answer = self._extractor.extract(task, sl)
                values[task.variable_name] = answer.value
                vr.extracted_value = answer.value
                vr.extraction_confidence = answer.confidence
                vr.extraction_consensus = answer.consensus
                vr.per_voter = answer.per_voter
                vr.stages.append(StageReport(
                    stage="extract", status="ok",
                    message=f"value={answer.value}, conf={answer.confidence}",
                ))
            except Exception as exc:
                values[task.variable_name] = None
                vr.error = f"{type(exc).__name__}: {exc}"
                vr.stages.append(StageReport(
                    stage="extract", status="error",
                    message=vr.error,
                    data={"traceback": traceback.format_exc()},
                ))

        # include known values from operational (not part of tasks)
        known = _known_values_from_operational(operational_placeholder=None,
                                               question_id=question_id,
                                               variable_names=None,
                                               operational_holder=getattr(self, "_last_operational", None))
        # NOTE: known values are filled by the caller; the resolve step
        # uses them via the pipeline normally. Here we fall back to the
        # operational list passed to run() by reading it back from the
        # report (kept simple for diagnostics).
        # For a full resolution, `run_full_audit` uses ResolverPipeline,
        # which handles known values properly. This orchestrator's job
        # is diagnostics - not duplicating pipeline logic.

        # evaluate the condition
        logic = spec.get("evaluation_logic") or {}
        condition = logic.get("condition") or spec.get("evaluation_condition") or ""
        on_true = logic.get("on_true", "") or ""
        on_false = logic.get("on_false", "") or ""

        try:
            result = self._evaluator.evaluate(
                condition, values,
                on_true=on_true, on_false=on_false,
            )
            q.condition_status = result["status"]
            q.condition_passed = result["is_true"] if result["status"] != "ERROR" else None
            q.condition_breakdown = [
                {"condition": c, "result": r}
                for c, r in zip(result["formatted_conditions"],
                                result["breakdown"])
            ]
            q.message = result["message"]

            if result["status"] == "TRUE":
                q.status = "OK"
            elif result["status"] == "FALSE":
                q.status = "MISMATCH"
            else:
                q.status = "ERROR"
        except Exception as exc:
            q.status = "ERROR"
            q.error = f"{type(exc).__name__}: {exc}"

        # redesign on persistent MISMATCH
        if q.status == "MISMATCH" and self._redesign is not None:
            sheets = self._collect_sheets_for_question(
                tasks, loaded_sheets,
            )
            if sheets:
                # Retrieve the most relevant chunks from the KB for THIS question
                full_content = ""
                if self._kb is not None and self._kb.is_ready():
                    query = f"{spec.get('question_text', '')} {spec.get('question_purpose', '')}".strip()
                    top = self._kb.search(query, top_k=3)
                    if top:
                        full_content = "\n\n".join(rc.to_markdown() for rc in top)
                        
                try:
                    from .llm import QuestionInfo
                    question = QuestionInfo(
                        question_id=question_id,
                        question_text=spec.get("question_text", ""),
                        question_purpose=(
                            spec.get("question_purpose")
                            or spec.get("question_porpose", "")
                        ),
                        evaluation_condition=condition,
                        original_data_points=spec.get("data_points_to_extract", []),
                        on_true=on_true, on_false=on_false,
                    )
                    redesign = self._redesign.redesign(
                        question=question,
                        extracted_values=values,
                        condition_breakdown=q.condition_breakdown,
                        sheets=sheets,
                        full_sheet_content=full_content,
                    )
                    if redesign.get("consensus"):
                        q.status = "REDESIGNED"
                        q.redesign_proposal = redesign["spec"]
                        q.message = "Original spec failed; a corrected spec was proposed."
                except Exception as exc:
                    q.error = f"redesign failed: {type(exc).__name__}: {exc}"

        return q

    # ---------------------------------------------------------------------

    @staticmethod
    def _collect_sheets_for_question(
        tasks: List[VariableTask],
        loaded_sheets: Dict[str, List[SheetData]],
    ) -> List[SheetData]:
        seen: set = set()
        all_sheets: List[SheetData] = []
        for task in tasks:
            if task.file_key in seen:
                continue
            seen.add(task.file_key)
            all_sheets.extend(loaded_sheets.get(task.file_key, []))
        return all_sheets


# ===========================================================================
# module-level helpers
# ===========================================================================

def _group_by_question(tasks: List[VariableTask]) -> Dict[str, List[VariableTask]]:
    grouped: Dict[str, List[VariableTask]] = {}
    for t in tasks:
        grouped.setdefault(t.question_id, []).append(t)
    return grouped


def _collect_file_keys(
    tasks: List[VariableTask],
    file_registry: Dict[str, Any],
) -> List[str]:
    """Every file_key any task refers to, deduplicated, in stable order."""
    seen: List[str] = []
    for t in tasks:
        if t.file_key not in seen:
            seen.append(t.file_key)
    return seen


def _find_var_report(
    reports: List[VariableReport],
    variable_name: str,
) -> VariableReport:
    for r in reports:
        if r.variable_name == variable_name:
            return r
    # Defensive: should never happen.
    r = VariableReport(question_id="?", variable_name=variable_name, file_key="?")
    reports.append(r)
    return r


def _known_values_from_operational(
    operational_placeholder,
    question_id,
    variable_names,
    operational_holder,
):
    """Placeholder for the diagnostics path.

    The full resolver (ResolverPipeline) handles known values by reading
    them from the input `operational` list. This orchestrator focuses
    on the diagnostic trace; if a caller needs correct known values,
    they should run through ResolverPipeline. Returning an empty dict
    here keeps this module independent of the operational list shape.
    """
    return {}

def _known_values_for_question(
    operational: List[Dict[str, Any]],
    question_id: str,
    variable_names: set,
) -> Dict[str, Optional[float]]:
    """Read the values the caller's own code already extracted for this
    question, normalized to floats (or None for placeholders)."""
    entry = next(
        (e for e in operational if e.get("question_id") == question_id),
        None,
    )
    if not entry:
        return {}

    out: Dict[str, Optional[float]] = {}
    for row in entry.get("extracted_data", []):
        name = row.get("متغیر")
        if name in variable_names:
            out[name] = _parse_numeric(row.get("مقدار استخراج‌شده"))
    return out


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

        
def _merge_operational(
    operational: List[Dict[str, Any]],
    questions: Dict[str, QuestionReport],
) -> List[Dict[str, Any]]:
    """Produce a new operational list (does not mutate the input).

    Existing extracted_data rows are preserved; any variable this run
    actually touched is overwritten with the new value. This keeps
    values the caller's own code resolved alongside freshly extracted
    ones, so downstream consumers see a complete picture.
    """
    from copy import deepcopy

    output: List[Dict[str, Any]] = []
    for entry in operational:
        new_entry = deepcopy(entry)
        q = questions.get(entry.get("question_id"))
        if q is None:
            output.append(new_entry)
            continue

        # Seed with whatever the operational entry already carried, then
        # overwrite/add with every variable the run touched.
        merged: Dict[str, str] = {}
        for row in entry.get("extracted_data", []):
            name = row.get("متغیر")
            if name:
                merged[name] = row.get("مقدار استخراج‌شده", "—")

        for v in q.variables:
            merged[v.variable_name] = (
                "—" if v.extracted_value is None
                else f"{v.extracted_value:,.0f}"
            )

        new_entry["extracted_data"] = [
            {"متغیر": name, "مقدار استخراج‌شده": val}
            for name, val in merged.items()
        ]

        new_entry["condition_breakdown"] = q.condition_breakdown or \
            new_entry.get("condition_breakdown", [])
        new_entry["message"] = q.message or new_entry.get("message", "")
        new_entry["status"] = q.status
        if q.redesign_proposal is not None:
            new_entry["redesign_proposal"] = q.redesign_proposal
        output.append(new_entry)
    return output


def _summarize(report: FullRunReport) -> Dict[str, Any]:
    statuses: Dict[str, int] = {}
    for q in report.questions:
        statuses[q.status] = statuses.get(q.status, 0) + 1

    file_statuses: Dict[str, int] = {}
    for info in report.files_loaded.values():
        s = info.get("status", "?")
        file_statuses[s] = file_statuses.get(s, 0) + 1

    index_statuses: Dict[str, int] = {}
    for info in report.files_indexed.values():
        s = info.get("status", "?")
        index_statuses[s] = index_statuses.get(s, 0) + 1

    errors: List[str] = []
    for file_key, info in report.files_loaded.items():
        if info.get("status") == "error":
            errors.append(f"load {file_key}: {info.get('error')}")
    for file_key, info in report.files_indexed.items():
        if info.get("status") == "error":
            errors.append(f"index {file_key}: {info.get('error')}")
    for q in report.questions:
        if q.error:
            errors.append(f"question {q.question_id}: {q.error}")

    return {
        "questions_total": len(report.questions),
        "questions_by_status": statuses,
        "files_loaded_by_status": file_statuses,
        "files_indexed_by_status": index_statuses,
        "errors": errors,
    }


# ===========================================================================
# convenience entry point
# ===========================================================================

def run_full_audit(
    checklist: List[Dict[str, Any]],
    operational: List[Dict[str, Any]],
    file_registry: Dict[str, Any],
    extractor,
    router: Optional[SizeRouter] = None,
    redesign_ensemble=None,
    sheet_descriptions: Optional[Dict[str, Dict[str, str]]] = None,
) -> FullRunReport:
    """Run every stage, return a diagnostic report + updated operational."""
    orchestrator = FullRunOrchestrator(
        extractor=extractor,
        router=router,
        redesign_ensemble=redesign_ensemble,
    )
    return orchestrator.run(
        checklist=checklist,
        operational=operational,
        file_registry=file_registry,
        sheet_descriptions=sheet_descriptions,
    )