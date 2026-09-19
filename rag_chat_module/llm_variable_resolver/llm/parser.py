"""
Extracts and validates the JSON that an LLM returns.

LLMs occasionally wrap JSON in ``` fences or append prose; both are
handled here so downstream code only ever sees a dict. Persian/Arabic
digits and thousand separators are normalized to plain floats.
"""

import json
import re
from typing import Any, Dict, Optional

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_DIGIT_TRANSLATION = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)


class AnswerParseError(Exception):
    """Raised when an LLM response cannot be turned into a usable dict."""


def _extract_json_block(raw: str) -> str:
    raw = (raw or "").strip()
    fenced = _FENCE_RE.search(raw)
    if fenced:
        return fenced.group(1).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise AnswerParseError(f"No JSON object found in response: {raw[:200]!r}")
    return raw[start : end + 1]


def coerce_number(value: Any) -> Optional[float]:
    """Normalize a value the LLM returned as a number; returns None for
    empty/placeholder values ("", "—", "-", "null", ...)."""
    if value is None:
        return None
    if isinstance(value, bool):  # avoid True -> 1.0
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text in {"—", "-", "--", "null", "None", "N/A"}:
        return None
    text = text.translate(_DIGIT_TRANSLATION).replace(",", "").replace("،", "")
    try:
        return float(text)
    except ValueError:
        return None


def parse_extraction_answer(raw: str) -> Dict[str, Any]:
    """Return a validated extraction answer."""
    data = json.loads(_extract_json_block(raw))
    if not isinstance(data, dict):
        raise AnswerParseError("Top-level JSON is not an object")

    confidence = str(data.get("confidence", "needs_review") or "").lower()
    if confidence not in {"high", "medium", "needs_review"}:
        confidence = "needs_review"

    return {
        "value": coerce_number(data.get("value")),
        "confidence": confidence,
        "matched_row": str(data.get("matched_row", "") or ""),
        "matched_column": str(data.get("matched_column", "") or ""),
        "source_sheet": str(data.get("source_sheet", "") or ""),
        "reasoning": str(data.get("reasoning", "") or ""),
        "raw": data,
    }


def parse_redesign_answer(raw: str) -> Dict[str, Any]:
    """Return a validated redesign spec."""
    data = json.loads(_extract_json_block(raw))
    if not isinstance(data, dict):
        raise AnswerParseError("Top-level JSON is not an object")

    points = data.get("data_points_to_extract") or []
    if not isinstance(points, list):
        points = [p for p in (points,) if isinstance(p, dict)]

    logic = data.get("evaluation_logic") or {}
    if not isinstance(logic, dict):
        logic = {}

    return {
        "is_evaluable_with_current_context": bool(
            data.get("is_evaluable_with_current_context", True)
        ),
        "data_points_to_extract": [p for p in points if isinstance(p, dict)],
        "evaluation_logic": {
            "condition": str(logic.get("condition", "") or ""),
            "on_false": str(logic.get("on_false", "") or ""),
            "on_true": str(logic.get("on_true", "") or ""),
        },
        "reasoning": str(data.get("reasoning", "") or ""),
        "raw": data,
    }