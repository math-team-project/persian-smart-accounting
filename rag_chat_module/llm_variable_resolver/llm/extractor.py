"""
EnsembleExtractor: single-variable extraction with N independent voters.

Each voter is one (LLMClient, ExtractionPromptBuilder) pair. They see
the exact same slice but may use different providers, models, or
prompt wordings - so their disagreements are genuine signals rather
than copy-paste noise.

Aggregation rule:
  - all voters agree on the same numeric value  -> confidence "high"
  - strict majority agrees                      -> confidence "medium"
  - no majority (or nobody produced a value)    -> confidence "needs_review"

The return value is deliberately audit-friendly: it carries the raw
per-voter list so M5 can log every opinion, not just the winner.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..models import RelevantSlice, VariableTask
from .client import LLMClient
from .parser import parse_extraction_answer
from .prompts import ExtractionPromptBuilder

_AGREEMENT_REL_TOL = 1e-6


@dataclass
class Voter:
    """One independent opinion about a variable's value."""

    name: str
    client: LLMClient
    prompt_builder: ExtractionPromptBuilder = field(default_factory=ExtractionPromptBuilder)


@dataclass
class EnsembleAnswer:
    variable_name: str
    value: Optional[float]
    confidence: str  # "high" | "medium" | "needs_review"
    consensus: bool
    message: str
    per_voter: List[Dict[str, Any]]


def _values_close(a: Optional[float], b: Optional[float]) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return math.isclose(a, b, rel_tol=_AGREEMENT_REL_TOL, abs_tol=0.0)


class EnsembleExtractor:
    def __init__(self, voters: List[Voter]):
        if not voters:
            raise ValueError("EnsembleExtractor needs at least one voter")
        self._voters = list(voters)

    def extract(self, task: VariableTask, relevant_slice: RelevantSlice) -> EnsembleAnswer:
        votes = [self._run_one(v, task, relevant_slice) for v in self._voters]
        return self._aggregate(task, votes)

    # -- internals ------------------------------------------------------

    @staticmethod
    def _run_one(voter: Voter, task: VariableTask, relevant_slice: RelevantSlice) -> Dict[str, Any]:
        from .parser import AnswerParseError
    
        try:
            system, user = voter.prompt_builder.build(task, relevant_slice)
            raw = voter.client.complete(system, user)
            try:
                parsed = parse_extraction_answer(raw)
            except AnswerParseError:
                # One retry with a much more imperative user prompt. Some
                # models (especially reasoning models) ignore the JSON-only
                # rule on the first pass but comply when told again.
                retry_user = (
                    user
                    + "\n\nIMPORTANT: Return ONLY the JSON object. "
                      "Do NOT write any prose, analysis, or explanation. "
                      "Start your response with the character { and end it with }."
                )
                raw = voter.client.complete(system, retry_user)
                parsed = parse_extraction_answer(raw)
    
            return {
                "voter": voter.name,
                "value": parsed["value"],
                "confidence": parsed["confidence"],
                "matched_row": parsed["matched_row"],
                "matched_column": parsed["matched_column"],
                "source_sheet": parsed["source_sheet"],
                "reasoning": parsed["reasoning"],
                "error": None,
            }
        except Exception as exc:
            return {
                "voter": voter.name,
                "value": None,
                "confidence": "needs_review",
                "matched_row": "",
                "matched_column": "",
                "source_sheet": "",
                "reasoning": "",
                "error": f"{type(exc).__name__}: {exc}",
            }

    def _aggregate(self, task: VariableTask, votes: List[Dict[str, Any]]) -> EnsembleAnswer:
        usable = [v for v in votes if v["value"] is not None]
        if not usable:
            return EnsembleAnswer(
                variable_name=task.variable_name,
                value=None,
                confidence="needs_review",
                consensus=False,
                message="No voter returned a usable value.",
                per_voter=votes,
            )

        clusters: List[List[Dict[str, Any]]] = []
        for vote in usable:
            for cluster in clusters:
                if _values_close(cluster[0]["value"], vote["value"]):
                    cluster.append(vote)
                    break
            else:
                clusters.append([vote])
        clusters.sort(key=len, reverse=True)

        best = clusters[0]
        total = len(votes)
        ratio = len(best) / total

        if len(best) == total:
            confidence, consensus = "high", True
            message = f"Unanimous agreement across {total} voters."
        elif ratio > 0.5:
            confidence, consensus = "medium", True
            message = f"Majority agreement ({len(best)}/{total} voters)."
        else:
            confidence, consensus = "needs_review", False
            message = f"No majority; strongest cluster has {len(best)}/{total} voters."

        return EnsembleAnswer(
            variable_name=task.variable_name,
            value=best[0]["value"],
            confidence=confidence,
            consensus=consensus,
            message=message,
            per_voter=votes,
        )