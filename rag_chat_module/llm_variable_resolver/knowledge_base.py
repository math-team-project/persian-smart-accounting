"""
Persistent knowledge base over every sheet of every file in a project.

Built once at the start of a run, then shared by:

  * the audit pipeline (its retrieval layer for level-3 escalation)
  * the chatbot (its source of truth for answering user questions)

Both consumers see the same index, so anything indexed for one is
immediately available to the other. The store itself is pluggable -
an in-memory store for tests, a Chroma-backed store for production.

Key design decisions:

  * One chunk per logical table, never one per file. This is what
    enables precise retrieval: a question about "موجودی نقد" should
    surface the deposits table, not the whole balance sheet.

  * Sheet descriptions from files.yaml are prepended to each chunk's
    metadata text BEFORE embedding. This is what makes the chatbot
    findable by topic even when the labels alone are generic.

  * The overall file description is also included (truncated), so
    queries that match a file's theme still work.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import pandas as pd

from .content_locator import load_sheets
from .models import SheetData
from .retrieval import (
    Chunk, Embedder, TableSegmenter, VectorStore,
    build_metadata_text,  # re-export from metadata_builder
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# report shapes
# ---------------------------------------------------------------------------

@dataclass
class IndexReport:
    files_indexed: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    total_chunks: int = 0
    total_files: int = 0
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "files_indexed": self.files_indexed,
            "total_chunks": self.total_chunks,
            "total_files": self.total_files,
            "errors": self.errors,
        }


@dataclass
class RetrievedChunk:
    """One search result with enough context to be rendered for the LLM."""
    chunk: Chunk
    score: float
    file_key: str
    sheet_names: List[str]

    def to_markdown(self) -> str:
        from .formatting import dataframe_to_labeled_text
        label = "/".join(self.sheet_names) or self.chunk.chunk_id
        return dataframe_to_labeled_text(self.chunk.dataframe, label)


# ---------------------------------------------------------------------------
# description helpers
# ---------------------------------------------------------------------------

def _extract_sheet_descriptions(file_descr: Any) -> Tuple[str, Dict[str, str]]:
    """Pull (overall_description, {sheet_name: description}) out of one
    files.yaml entry. Accepts any of `sheets`, `tables`, or `pages` as
    the sub-container key - real files.yaml uses all three."""
    if not isinstance(file_descr, dict):
        return "", {}

    overall = str(file_descr.get("overall_description", "") or "")

    per_sheet: Dict[str, str] = {}
    for key in ("sheets", "tables", "pages"):
        container = file_descr.get(key)
        if isinstance(container, dict):
            for name, spec in container.items():
                if isinstance(spec, dict) and "description" in spec:
                    per_sheet[str(name)] = str(spec["description"])
    return overall, per_sheet


# ---------------------------------------------------------------------------
# knowledge base
# ---------------------------------------------------------------------------

class KnowledgeBase:
    """One index over every sheet in every project file."""

    def __init__(
        self,
        embedder: Embedder,
        vector_store_factory: Callable[[], VectorStore],
        segmenter: Optional[TableSegmenter] = None,
    ):
        self._embedder = embedder
        self._vector_store_factory = vector_store_factory
        self._segmenter = segmenter or TableSegmenter()
        self._store: Optional[VectorStore] = None
        self._chunks_by_id: Dict[str, Chunk] = {}
        self._fitted = False

    # -----------------------------------------------------------------
    # indexing
    # -----------------------------------------------------------------

    def index_files(
        self,
        file_registry: Dict[str, Any],
        sheet_descriptions: Optional[Dict[str, Any]] = None,
    ) -> IndexReport:
        """Build (or rebuild) the index over every file in the registry."""
        report = IndexReport()
        sheet_descriptions = sheet_descriptions or {}

        all_chunks: List[Chunk] = []

        for file_key, source in file_registry.items():
            try:
                sheets = load_sheets(source)
                chunks = self._segmenter.build_chunks(file_key, sheets)

                overall, per_sheet = _extract_sheet_descriptions(
                    sheet_descriptions.get(file_key),
                )
                self._attach_descriptions(chunks, overall, per_sheet)

                all_chunks.extend(chunks)
                report.files_indexed[file_key] = {
                    "status": "ok",
                    "sheets": len(sheets),
                    "chunks": len(chunks),
                }
                report.total_chunks += len(chunks)
                report.total_files += 1
            except Exception as exc:
                report.files_indexed[file_key] = {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
                report.errors.append(f"{file_key}: {exc}")

        if not all_chunks:
            logger.warning("KnowledgeBase.index_files: no chunks to index.")
            return report

        # Fit the embedder over the full corpus once, then embed each
        # chunk. The store is built after fitting so its vectors live in
        # the same space.
        self._embedder.fit([c.metadata_text for c in all_chunks])
        self._fitted = True

        store = self._vector_store_factory()
        for chunk in all_chunks:
            self._chunks_by_id[chunk.chunk_id] = chunk
            store.add(chunk, self._embedder.embed(chunk.metadata_text))
        self._store = store

        return report

    @staticmethod
    def _attach_descriptions(
        chunks: List[Chunk],
        overall: str,
        per_sheet: Dict[str, str],
    ) -> None:
        """Prepend short description text to each chunk's metadata text.

        Descriptions are truncated so they add topical signal without
        drowning out the labels (the labels are what actually
        discriminate one chunk from another within the same file).
        """
        for chunk in chunks:
            parts: List[str] = []
            if overall:
                parts.append(overall[:400])
            for sheet_name in chunk.source_sheet_names:
                desc = per_sheet.get(sheet_name)
                if desc:
                    parts.append(desc[:400])
                    break
            # The original label text always comes last so it dominates
            # the token budget after description context.
            parts.append(build_metadata_text(chunk))
            chunk.metadata_text = "\n".join(p for p in parts if p)

    # -----------------------------------------------------------------
    # search
    # -----------------------------------------------------------------

    def is_ready(self) -> bool:
        return self._fitted and self._store is not None

    def search(
        self,
        query: str,
        top_k: int = 3,
        min_similarity: float = 0.0,
        file_keys: Optional[List[str]] = None,
    ) -> List[RetrievedChunk]:
        """Vector search over the whole index (or a subset of files)."""
        if not self.is_ready():
            return []

        query_vec = self._embedder.embed(query)
        # Over-fetch when filtering by file_key so we still end up with
        # top_k after the filter.
        fetch_k = top_k if not file_keys else max(top_k * 5, 20)
        raw = self._store.search(query_vec, fetch_k)

        results: List[RetrievedChunk] = []
        for chunk, score in raw:
            if score < min_similarity:
                continue
            if file_keys and chunk.file_key not in file_keys:
                continue
            results.append(RetrievedChunk(
                chunk=chunk,
                score=float(score),
                file_key=chunk.file_key,
                sheet_names=list(chunk.source_sheet_names),
            ))
            if len(results) >= top_k:
                break
        return results

    def get_chunks_by_sheet_names(
        self,
        file_key: str,
        sheet_names: List[str],
    ) -> List[RetrievedChunk]:
        """Return all chunks belonging to specific sheet names of a file.

        Used after an LLM router has named the sheets it wants, so we
        can skip the vector search entirely and pull their full content.
        """
        out: List[RetrievedChunk] = []
        wanted = {s.strip() for s in sheet_names}
        for chunk in self._chunks_by_id.values():
            if chunk.file_key != file_key:
                continue
            if any(s.strip() in wanted for s in chunk.source_sheet_names):
                out.append(RetrievedChunk(
                    chunk=chunk, score=1.0,
                    file_key=chunk.file_key,
                    sheet_names=list(chunk.source_sheet_names),
                ))
        return out

    def list_sheets(self) -> Dict[str, List[str]]:
        """Every (file_key -> unique sheet names) currently indexed."""
        out: Dict[str, set] = {}
        for chunk in self._chunks_by_id.values():
            out.setdefault(chunk.file_key, set()).update(
                chunk.source_sheet_names
            )
        return {k: sorted(v) for k, v in out.items()}