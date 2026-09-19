"""
Finds which sheet(s)/section(s) of a parsed file are relevant to a
given VariableTask - independent of whether that file was an Excel
workbook, a converted PDF, or a markdown document (they all reach
this point as a list of SheetData).

Resolution proceeds through three cascading strategies, cheapest and
most confident first:

1. Name match - `sheet_anchor` matches a sheet's own name, after
   normalization.
2. Description match - `sheet_anchor` matches a manually supplied
   one-line description of a sheet. This covers workbooks with
   generic sheet names (e.g. "Sheet1") where the anchor is a concept,
   not a literal title.
3. Content-scan fallback - when neither of the above settles on a
   single sheet, every remaining candidate sheet is scanned for the
   variable's `row_identifier` / `column_identifier` text.

If a step yields exactly one confident match, that sheet's content is
returned as the final answer. Otherwise every remaining candidate is
bundled together, clearly labeled, and flagged `is_ambiguous=True` -
this module never guesses; the final call is left to the LLM stage
that follows it in the pipeline.
"""

from typing import Callable, List, Optional

import pandas as pd

from .formatting import dataframe_to_labeled_text
from .models import RelevantSlice, SheetCandidate, SheetData, VariableTask

NormalizeFn = Callable[[str], str]

# Below this length, a "contains" match is too easy to trigger by pure
# coincidence - e.g. a real workbook in this pipeline has sheets named
# with a single Persian letter ("ت", "ع"), which would otherwise match
# almost any anchor text that happens to contain that common letter.
# Exact-equality matches are never affected by this guard.
_MIN_CONTAINMENT_LENGTH = 3


def _identity(text: str) -> str:
    """Default normalization: a no-op, until a real one is injected."""
    return text or ""


class SheetResolver:
    """Resolves a VariableTask's sheet_anchor against a list of SheetData."""

    def __init__(self, normalize_fn: Optional[NormalizeFn] = None):
        self._normalize = normalize_fn or _identity

    def resolve(self, sheets: List[SheetData], task: VariableTask) -> RelevantSlice:
        if not sheets:
            return RelevantSlice("", [], True, [{"error": "no_sheets_loaded"}], 0)

        anchor = self._normalize(task.sheet_anchor)

        name_matches = self._match_by_name(sheets, anchor) if anchor else []
        if len(name_matches) == 1:
            return self._confident_slice(name_matches[0], "name_match")

        description_matches = self._match_by_description(sheets, anchor) if anchor else []
        if len(description_matches) == 1:
            return self._confident_slice(description_matches[0], "description_match")

        # Narrow the content scan to whichever step produced candidates,
        # falling back to every sheet only when nothing narrowed it down at all.
        search_scope = name_matches or description_matches or sheets
        candidates = self._scan_content(search_scope, task)

        # Exactly one sheet survived the content scan -> that is a confident
        # answer too, even though it was found through the fallback path.
        if len(candidates) == 1:
            return self._confident_slice(candidates[0].sheet, "content_scan")

        if not candidates:
            candidates = [SheetCandidate(sheet=s, match_reason="no_match") for s in search_scope]

        return self._ambiguous_slice(candidates)

    # -- matching strategies --------------------------------------------

    def _match_by_name(self, sheets: List[SheetData], anchor: str) -> List[SheetData]:
        exact = [s for s in sheets if self._normalize(s.name) == anchor]
        if exact:
            return exact
        return [s for s in sheets if self._contains_safely(anchor, self._normalize(s.name))]

    def _match_by_description(self, sheets: List[SheetData], anchor: str) -> List[SheetData]:
        described = [s for s in sheets if s.description]
        return [s for s in described if self._contains_safely(anchor, self._normalize(s.description))]

    @staticmethod
    def _contains_safely(text_a: str, text_b: str) -> bool:
        """Substring match, guarded against trivial matches on very short text."""
        if min(len(text_a), len(text_b)) < _MIN_CONTAINMENT_LENGTH:
            return False
        return text_a in text_b or text_b in text_a

    def _scan_content(self, sheets: List[SheetData], task: VariableTask) -> List[SheetCandidate]:
        identifiers = [i for i in (task.row_identifier, task.column_identifier) if i]
        if not identifiers:
            return []

        candidates = []
        for sheet in sheets:
            flat_text = self._normalize(self._flatten(sheet.dataframe))
            score = sum(1 for ident in identifiers if self._normalize(ident) in flat_text)
            if score > 0:
                candidates.append(SheetCandidate(sheet, "content_scan", score))

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    @staticmethod
    def _flatten(df: pd.DataFrame) -> str:
        return df.astype(str).to_string()

    # -- slice building ---------------------------------------------------

    def _confident_slice(self, sheet: SheetData, reason: str) -> RelevantSlice:
        text = dataframe_to_labeled_text(sheet.dataframe, sheet.name)
        return RelevantSlice(
            content=text,
            matched_sheet_names=[sheet.name],
            is_ambiguous=False,
            candidates_meta=[{"sheet": sheet.name, "reason": reason}],
            size_estimate=len(text),
            resolution_method="sheet_resolver",
        )

    def _ambiguous_slice(self, candidates: List[SheetCandidate]) -> RelevantSlice:
        content = "\n".join(dataframe_to_labeled_text(c.sheet.dataframe, c.sheet.name) for c in candidates)
        return RelevantSlice(
            content=content,
            matched_sheet_names=[c.sheet.name for c in candidates],
            is_ambiguous=True,
            candidates_meta=[
                {"sheet": c.sheet.name, "reason": c.match_reason, "score": str(c.score)}
                for c in candidates
            ],
            size_estimate=len(content),
            resolution_method="sheet_resolver",
        )
