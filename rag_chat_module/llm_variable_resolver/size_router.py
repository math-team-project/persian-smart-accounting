"""
Decides, per variable, which resolution strategy is worth running:

1. "slice_prompt"     - SheetResolver already found a single confident
                         sheet, or narrowed ambiguity down to a small
                         enough set of candidates to hand the LLM
                         directly. Cheapest, tried first, always.
2. "full_file_prompt" - SheetResolver could not narrow things down at
                         all, but the whole file is small enough that
                         sending every sheet to the LLM is still cheap.
3. "chunk_retrieval"  - the file is too large for (1) or (2) to be
                         practical (e.g. a 31-sheet financial
                         statement) - semantic retrieval (level 3) is
                         used instead, which only touches the file
                         once (index_file is cached) no matter how
                         many variables query it.

SheetResolver (levels 1/2) is deterministic and nearly free, so it is
always tried first; level 3 is only reached when it is genuinely
needed - see the real-file test that motivated this module, where
level 1/2 alone already solved most cases correctly.
"""

from typing import List, Optional

from .formatting import dataframe_to_labeled_text
from .models import RelevantSlice, SheetData, VariableTask
from .retrieval import ChunkRetriever
from .sheet_resolver import SheetResolver

# Tunable thresholds (character counts, used as a cheap proxy for token count).
DEFAULT_SLICE_ACCEPT_THRESHOLD = 8_000       # ambiguous slice small enough to accept as-is
DEFAULT_FULL_FILE_THRESHOLD = 20_000         # whole file small enough to send in full


class SizeRouter:
    """Routes one VariableTask to the cheapest strategy likely to work."""

    def __init__(
        self,
        sheet_resolver: Optional[SheetResolver] = None,
        chunk_retriever: Optional[ChunkRetriever] = None,
        slice_accept_threshold: int = DEFAULT_SLICE_ACCEPT_THRESHOLD,
        full_file_threshold: int = DEFAULT_FULL_FILE_THRESHOLD,
    ):
        self._sheet_resolver = sheet_resolver or SheetResolver()
        # Shared across calls so its per-file chunk index is cached,
        # not rebuilt for every variable that points at the same file.
        self._chunk_retriever = chunk_retriever or ChunkRetriever()
        self._slice_accept_threshold = slice_accept_threshold
        self._full_file_threshold = full_file_threshold

    def resolve(self, task: VariableTask, sheets: List[SheetData]) -> RelevantSlice:
        slice_result = self._sheet_resolver.resolve(sheets, task)

        if not slice_result.is_ambiguous:
            return self._with_method(slice_result, "slice_prompt")

        if not self._is_total_failure(slice_result) and slice_result.size_estimate <= self._slice_accept_threshold:
            # Already narrowed to a small, curated set of candidates - good
            # enough to let the LLM disambiguate cheaply, no need to escalate.
            return self._with_method(slice_result, "slice_prompt")

        full_file_slice = self._render_full_file(sheets)
        if full_file_slice.size_estimate <= self._full_file_threshold:
            return self._with_method(full_file_slice, "full_file_prompt")

        # File is too large for either cheap option - fall back to semantic
        # retrieval, which only pays the indexing cost once per file.
        self._chunk_retriever.index_file(task.file_key, sheets)
        retrieval_result = self._chunk_retriever.retrieve(task)
        # The retriever's error paths (e.g. "no chunk above similarity threshold")
        # leave resolution_method empty; only successful retrievals set it.
        # Tag it here so every caller sees which branch SizeRouter chose.
        return self._with_method(retrieval_result, "chunk_retrieval")
        
    @staticmethod
    def _is_total_failure(slice_result: RelevantSlice) -> bool:
        """True when SheetResolver found nothing at all (every sheet
        came back as a "no_match" candidate) rather than a genuinely
        narrowed-down, curated set of candidates."""
        if not slice_result.candidates_meta:
            return True
        return all(meta.get("reason") == "no_match" for meta in slice_result.candidates_meta)

    @staticmethod
    def _render_full_file(sheets: List[SheetData]) -> RelevantSlice:
        content = "\n".join(dataframe_to_labeled_text(s.dataframe, s.name) for s in sheets)
        return RelevantSlice(
            content=content,
            matched_sheet_names=[s.name for s in sheets],
            is_ambiguous=True,
            candidates_meta=[{"sheet": s.name, "reason": "full_file"} for s in sheets],
            size_estimate=len(content),
        )

    @staticmethod
    def _with_method(slice_result: RelevantSlice, method: str) -> RelevantSlice:
        slice_result.resolution_method = method
        return slice_result
