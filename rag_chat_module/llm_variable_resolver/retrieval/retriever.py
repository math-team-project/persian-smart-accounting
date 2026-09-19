"""
Public entry point for the level-3 RAG fallback: chunk, embed, and
retrieve. This is used when the deterministic sheet-name matching
(SheetResolver) is not reliable or fast enough for a file - typically
large, multi-sheet files such as full financial statements - and a
semantic search over each chunk's structural labels is needed instead.

Chunking and embedding for a file are cached by file_key, so multiple
variables pointing at the same file (a common case) do not repeat
that work.
"""

from typing import Callable, Dict, List, Optional, Type

from ..formatting import dataframe_to_labeled_text
from ..models import RelevantSlice, SheetData, VariableTask
from .chunk_models import Chunk
from .embedder import Embedder, LocalTfidfEmbedder
from .metadata_builder import build_metadata_text
from .segmenter import TableSegmenter
from .vector_store import InMemoryVectorStore, VectorStore

# A similarity below this is treated as "no real match" rather than
# forcing a low-quality guess through to the LLM stage.
_MIN_SIMILARITY = 0.05

# Minimum gap required between the top-1 and top-2 similarity scores
# before the top match is trusted on its own. Without this, a weak
# embedder (or two genuinely similar tables) can rank the wrong sheet
# first by a razor-thin margin - this was observed in practice with
# the offline TF-IDF default. Below this margin, both candidates are
# handed to the LLM stage instead of silently trusting rank #1.
_MIN_CONFIDENCE_MARGIN = 0.1


class ChunkRetriever:
    """Builds a per-file chunk index once, then answers queries against it."""

    def __init__(
        self,
        embedder: Optional[Embedder] = None,
        vector_store_factory: Type[VectorStore] = InMemoryVectorStore,
        segmenter: Optional[TableSegmenter] = None,
        top_k: int = 3,
        min_similarity: float = _MIN_SIMILARITY,
        min_confidence_margin: float = _MIN_CONFIDENCE_MARGIN,
    ):
        self._embedder = embedder or LocalTfidfEmbedder()
        self._vector_store_factory = vector_store_factory
        self._segmenter = segmenter or TableSegmenter()
        self._top_k = top_k
        self._min_similarity = min_similarity
        self._min_confidence_margin = min_confidence_margin
        self._stores: Dict[str, VectorStore] = {}

    def index_file(self, file_key: str, sheets: List[SheetData]) -> None:
        """Builds (or reuses) the chunk index for one file."""
        if file_key in self._stores:
            return

        chunks = self._segmenter.build_chunks(file_key, sheets)
        for chunk in chunks:
            chunk.metadata_text = build_metadata_text(chunk)

        self._embedder.fit([c.metadata_text for c in chunks])

        store = self._vector_store_factory()
        for chunk in chunks:
            store.add(chunk, self._embedder.embed(chunk.metadata_text))
        self._stores[file_key] = store

    def retrieve(self, task: VariableTask) -> RelevantSlice:
        store = self._stores.get(task.file_key)
        if store is None:
            return RelevantSlice("", [], True, [{"error": f"File not indexed: '{task.file_key}'"}])

        query_vector = self._embedder.embed(self._build_query_text(task))
        # Fetch at least 2 results so a confidence-margin check is possible
        # even when the caller only wants the single best match.
        raw_results = store.search(query_vector, max(self._top_k, 2))
        relevant = [(c, s) for c, s in raw_results if s >= self._min_similarity]

        if not relevant:
            return RelevantSlice("", [], True, [{"error": "no_chunk_above_similarity_threshold"}])

        if len(relevant) == 1:
            return self._to_slice(relevant, is_ambiguous=False)

        margin = relevant[0][1] - relevant[1][1]
        if margin >= self._min_confidence_margin:
            # Top match is decisively ahead of the runner-up: trust it alone.
            return self._to_slice(relevant[:1], is_ambiguous=False)

        # Too close to call - let the LLM stage decide between the candidates
        # rather than silently trusting a rank that could be wrong.
        return self._to_slice(relevant[: self._top_k], is_ambiguous=True)

    @staticmethod
    def _build_query_text(task: VariableTask) -> str:
        parts = [task.sheet_anchor, task.row_identifier, task.column_identifier, task.question_purpose]
        return " | ".join(p for p in parts if p)

    @staticmethod
    def _to_slice(matches, is_ambiguous: bool) -> RelevantSlice:
        content = "\n".join(
            dataframe_to_labeled_text(chunk.dataframe, "/".join(chunk.source_sheet_names))
            for chunk, _ in matches
        )
        return RelevantSlice(
            content=content,
            matched_sheet_names=[name for chunk, _ in matches for name in chunk.source_sheet_names],
            is_ambiguous=is_ambiguous,
            candidates_meta=[
                {"sheet": "/".join(chunk.source_sheet_names), "similarity": f"{score:.3f}"}
                for chunk, score in matches
            ],
            size_estimate=len(content),
            resolution_method="chunk_retrieval",
        )
