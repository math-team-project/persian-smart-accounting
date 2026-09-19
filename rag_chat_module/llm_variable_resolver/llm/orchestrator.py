"""
QuestionOrchestrator: the full M4 flow for ONE checklist question.

Flow:
  1. For every missing variable of the question, run EnsembleExtractor
     on its RelevantSlice (produced by M3).
  2. Re-evaluate the question's evaluation_condition with the values
     now known (MathConditionEvaluator is reused - no parallel logic).
  3. If the condition still fails (mismatch, not missing), and if the
     caller supplied the file's sheets, run RedesignEnsemble on the
     sheet templates only (never the numeric content).
  4. Return a QuestionResolution ready to be serialized by M5.
"""

from __future__ import annotations   # <-- allows forward-referenced type hints

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ..condition_evaluator import MathConditionEvaluator
from ..models import RelevantSlice, SheetData, VariableTask
from ..sheet_template_builder import build_sheet_templates
from .client import LLMClient
from .extractor import EnsembleAnswer, EnsembleExtractor
from .parser import parse_redesign_answer
from .prompts import RedesignPromptBuilder


# ---------------------------------------------------------------------------
# Public data shapes
# ---------------------------------------------------------------------------

@dataclass
class QuestionInfo:
    """The minimum question metadata the orchestrator needs."""

    question_id: str
    question_text: str
    question_purpose: str
    evaluation_condition: str
    original_data_points: List[Dict[str, Any]] = field(default_factory=list)
    on_true: str = ""
    on_false: str = ""


@dataclass
class QuestionResolution:
    """Operational-format result for one question (serializable)."""

    question_id: str
    question_text: str
    question_purpose: str
    is_evaluable: bool
    evaluation_condition: str
    condition_breakdown: List[Dict[str, Any]]
    extracted_data: List[Dict[str, Any]]
    message: str
    status: str  # "OK" | "MISMATCH" | "NEEDS_REVIEW" | "REDESIGNED"
    redesign: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        if payload.get("redesign") is None:
            payload.pop("redesign", None)
        return payload


# ---------------------------------------------------------------------------
# Redesign ensemble
# ---------------------------------------------------------------------------

@dataclass
class RedesignVoter:
    """One independent redesign opinion."""

    name: str
    client: LLMClient
    prompt_builder: RedesignPromptBuilder = field(default_factory=RedesignPromptBuilder)


class RedesignEnsemble:
    """Runs N redesign voters and returns the majority answer if any."""

    def __init__(self, voters: List[RedesignVoter]):
        if not voters:
            raise ValueError("RedesignEnsemble needs at least one voter")
        self._voters = list(voters)

    def redesign(
        self,
        question: QuestionInfo,
        extracted_values: Dict[str, Any],
        condition_breakdown: List[Dict[str, str]],
        sheets: List[SheetData],
        full_sheet_content: str = "",    # ← NEW
    ) -> Dict[str, Any]:
        templates = build_sheet_templates(sheets)
        votes = [
            self._run_one(v, question, extracted_values,
                          condition_breakdown, templates, full_sheet_content)
            for v in self._voters
        ]
        consensus = self._pick_majority(votes)
        return {
            "consensus": consensus is not None,
            "per_voter": votes,
            "spec": consensus or (votes[0]["parsed"] if votes else {}),
        }

    @staticmethod
    def _run_one(voter, question, extracted_values, condition_breakdown,
                 templates, full_sheet_content) -> Dict[str, Any]:
        try:
            system, user = voter.prompt_builder.build(
                question_text=question.question_text,
                question_purpose=question.question_purpose,
                original_data_points=question.original_data_points,
                extracted_values=extracted_values,
                condition_breakdown=condition_breakdown,
                sheet_templates=templates,
                full_sheet_content=full_sheet_content,
            )
            raw = voter.client.complete(system, user)
            parsed = parse_redesign_answer(raw)
            return {"voter": voter.name, "parsed": parsed, "error": None}
        except Exception as exc:
            return {
                "voter": voter.name,
                "parsed": {},
                "error": f"{type(exc).__name__}: {exc}",
            }

    @staticmethod
    def _pick_majority(votes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        buckets: Dict[tuple, List[Dict[str, Any]]] = {}
        for vote in votes:
            parsed = vote.get("parsed") or {}
            logic = parsed.get("evaluation_logic") or {}
            key = (
                parsed.get("is_evaluable_with_current_context"),
                logic.get("condition", ""),
            )
            buckets.setdefault(key, []).append(vote)

        if not buckets:
            return None
        best = max(buckets.values(), key=len)
        if len(best) * 2 <= len(votes):
            return None
        return best[0]["parsed"]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class QuestionOrchestrator:
    def __init__(
        self,
        extractor: EnsembleExtractor,
        evaluator=None,
        redesign_ensemble: Optional[RedesignEnsemble] = None,
    ):
        self._extractor = extractor
        self._evaluator = evaluator or MathConditionEvaluator()
        self._redesign = redesign_ensemble

    def resolve_question(
        self,
        question: QuestionInfo,
        tasks: List[VariableTask],
        slices: Dict[str, RelevantSlice],
        *,
        known_values: Optional[Dict[str, Optional[float]]] = None,
        sheets: Optional[List[SheetData]] = None,
    ) -> QuestionResolution:
        values: Dict[str, Optional[float]] = dict(known_values or {})
        per_variable_audit: Dict[str, EnsembleAnswer] = {}

        # 1) Fill every missing variable via the ensemble.
        for task in tasks:
            if values.get(task.variable_name) is not None:
                continue
            slice_ = slices.get(task.variable_name)
            if slice_ is None:
                continue
            answer = self._extractor.extract(task, slice_)
            values[task.variable_name] = answer.value
            per_variable_audit[task.variable_name] = answer

        # 2) Re-evaluate the condition with whatever we now have.
        passed, breakdown = self._evaluate(
            question.evaluation_condition,
            values,
            on_true=question.on_true,
            on_false=question.on_false,
        )

        # 3) Decide the base status.
        missing = [name for name, v in values.items() if v is None]
        if missing:
            status = "NEEDS_REVIEW"
            message = f"Could not extract: {', '.join(missing)}."
        elif passed is True:
            status = "OK"
            message = question.on_true or "Condition satisfied after LLM extraction."
        elif passed is False:
            status = "MISMATCH"
            message = question.on_false or "Extraction succeeded but condition is still false."
        else:
            status = "NEEDS_REVIEW"
            message = "Condition could not be evaluated."

        redesign_payload: Optional[Dict[str, Any]] = None

        # 4) On persistent MISMATCH, try redesigning (only if caller allowed it).
        if status == "MISMATCH" and self._redesign is not None and sheets:
            redesign_payload = self._redesign.redesign(
                question=question,
                extracted_values=values,
                condition_breakdown=breakdown,
                sheets=sheets,
            )
            if redesign_payload.get("consensus"):
                status = "REDESIGNED"
                message = "Original spec failed; a corrected spec was proposed."

        return QuestionResolution(
            question_id=question.question_id,
            question_text=question.question_text,
            question_purpose=question.question_purpose,
            is_evaluable=not missing,
            evaluation_condition=question.evaluation_condition,
            condition_breakdown=breakdown,
            extracted_data=self._render_extracted(values),
            message=message,
            status=status,
            redesign=redesign_payload,
        )

    # -- helpers ---------------------------------------------------------

    def _evaluate(
        self,
        expression: str,
        values: Dict[str, Optional[float]],
        on_true: str = "",
        on_false: str = "",
    ) -> tuple[Optional[bool], List[Dict[str, Any]]]:
        """Evaluate the condition and return a JSON-ready breakdown.

        MathConditionEvaluator returns a list of
        {"condition": "[a == b]", "result": True|False|"FAILED"}.
        """
        if not expression:
            return None, []
        try:
            passed, breakdown, _message = self._evaluator.describe_and_evaluate(
                expression, values, on_true=on_true, on_false=on_false,
            )
        except Exception as exc:
            return None, [{
                "condition": expression,
                "result": f"ERROR: {type(exc).__name__}: {exc}",
            }]
        return passed, breakdown

    @staticmethod
    def _render_extracted(values: Dict[str, Optional[float]]) -> List[Dict[str, str]]:
        rendered = []
        for name, value in values.items():
            rendered.append({
                "متغیر": name,
                "مقدار استخراج‌شده": "—" if value is None else f"{value:,.0f}",
            })
        return rendered