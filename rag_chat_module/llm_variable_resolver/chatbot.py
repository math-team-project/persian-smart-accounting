"""
Single-function chatbot over an indexed knowledge base.

Flow (two LLM calls, in this order):

  1. Router call - reads the sheet catalog (file + sheet names +
     descriptions) and picks the most promising candidates for the
     user's question. This keeps the next call's context small.

  2. Answer call - receives the FULL content of the router's picks
     (retrieved via the vector DB so we get the exact chunks, not
     whole files) and writes the final answer.

If no catalog is available, the router call is skipped and vector
search runs directly against the whole index.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .knowledge_base import KnowledgeBase, RetrievedChunk
from .llm import LLMClient
from .llm.parser import _extract_json_block  # reuse the fenced-JSON parser

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# result shape
# ---------------------------------------------------------------------------

@dataclass
class ChatSource:
    file_key: str
    sheet_names: List[str]
    chunk_id: str
    similarity: float
    snippet: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_key": self.file_key,
            "sheet_names": self.sheet_names,
            "chunk_id": self.chunk_id,
            "similarity": round(self.similarity, 3),
            "snippet": self.snippet,
        }


@dataclass
class ChatAnswer:
    question: str
    answer: str
    sources: List[ChatSource] = field(default_factory=list)
    confidence: str = "medium"     # "high" | "medium" | "needs_review"
    route_reasoning: str = ""
    raw_llm_response: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "confidence": self.confidence,
            "sources": [s.to_dict() for s in self.sources],
            "route_reasoning": self.route_reasoning,
        }


# ---------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------

_ROUTER_SYSTEM = """\
You are a routing assistant for a Persian financial audit system.

You receive a user's question and a catalog of every sheet available
in the project. Pick the 1-3 sheets most likely to contain the answer.

Output exactly one JSON object, no prose:
{
  "file_key": "<one file key from the catalog>",
  "sheet_names": ["<name1>", "<name2>"],
  "reasoning": "<one short sentence>"
}

Rules:
  - Prefer sheets whose description mentions the exact topic of the question.
  - If nothing looks relevant, return an empty sheet_names list.
  - Never invent file_keys or sheet names that are not in the catalog."""


_ANSWER_SYSTEM = """\
You are a meticulous Persian financial-audit assistant.

You will receive a user question and the complete content of a small
set of sheets retrieved from the project's files. Answer the question
using ONLY that content. If the answer is not present, say so
explicitly - never guess or infer beyond the tables.

Output exactly one JSON object:
{
  "answer": "<a concise Persian answer, one paragraph max>",
  "confidence": "high" | "medium" | "needs_review",
  "sources": [
    {"file_key": "<...>", "sheet_names": ["<...>"], "snippet": "<one-line quote>"}
  ]
}"""


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def ask(
    question: str,
    knowledge_base: KnowledgeBase,
    llm_client: LLMClient,
    sheet_catalog: Optional[Dict[str, Any]] = None,
    top_k: int = 3,
    use_router: bool = True,
    min_similarity: float = 0.0,
) -> ChatAnswer:
    """Answer `question` using the knowledge base and (optionally) a router."""
    if not knowledge_base.is_ready():
        return ChatAnswer(
            question=question,
            answer="پایگاه دانش آماده نیست. ابتدا فایل‌ها را ایندکس کنید.",
            confidence="needs_review",
        )

    # --- Stage 1: router ------------------------------------------------
    route_reasoning = ""
    file_keys_filter: Optional[List[str]] = None

    if use_router and sheet_catalog:
        route = _route_via_descriptions(question, sheet_catalog, llm_client)
        route_reasoning = route.get("reasoning", "")
        if route.get("file_key"):
            file_keys_filter = [route["file_key"]]
        # If the router also picked specific sheet names, fetch those
        # sheets' chunks directly (skips the vector search entirely).
        if route.get("file_key") and route.get("sheet_names"):
            direct = knowledge_base.get_chunks_by_sheet_names(
                route["file_key"], route["sheet_names"],
            )
            if direct:
                chunks = _rank_by_query(
                    direct, question, knowledge_base, top_k=top_k,
                )
                return _answer_with_llm(question, chunks, llm_client, route_reasoning)

    # --- Stage 2: vector search ----------------------------------------
    chunks = knowledge_base.search(
        question,
        top_k=top_k,
        min_similarity=min_similarity,
        file_keys=file_keys_filter,
    )

    if not chunks:
        return ChatAnswer(
            question=question,
            answer="در فایل‌های موجود پاسخی برای این سؤال یافت نشد.",
            confidence="needs_review",
            route_reasoning=route_reasoning,
        )

    # --- Stage 3: answer ------------------------------------------------
    return _answer_with_llm(question, chunks, llm_client, route_reasoning)


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------

def _route_via_descriptions(
    question: str,
    catalog: Dict[str, Any],
    llm_client: LLMClient,
) -> Dict[str, Any]:
    """One LLM call to pick candidate sheets from the descriptions."""
    catalog_text = _render_catalog(catalog)
    user = f"""\
User question:
{question}

Available sheets (file_key / sheet_name: description):
{catalog_text}

Return the JSON object now."""
    try:
        raw = llm_client.complete(_ROUTER_SYSTEM, user, max_tokens=512)
        parsed = json.loads(_extract_json_block(raw))
        if not isinstance(parsed, dict):
            return {}
        sheet_names = parsed.get("sheet_names") or []
        if isinstance(sheet_names, str):
            sheet_names = [sheet_names]
        return {
            "file_key": parsed.get("file_key"),
            "sheet_names": [str(s) for s in sheet_names],
            "reasoning": str(parsed.get("reasoning", "") or ""),
        }
    except Exception as exc:
        logger.warning("Router LLM failed: %s", exc)
        return {}


def _render_catalog(catalog: Dict[str, Any]) -> str:
    """Flatten the nested sheet_descriptions into one text block."""
    lines: List[str] = []
    for file_key, entry in catalog.items():
        overall = ""
        per_sheet: Dict[str, str] = {}
        if isinstance(entry, dict):
            overall = str(entry.get("overall_description", "") or "")
            for key in ("sheets", "tables", "pages"):
                container = entry.get(key)
                if isinstance(container, dict):
                    for name, spec in container.items():
                        if isinstance(spec, dict) and "description" in spec:
                            per_sheet[str(name)] = str(spec["description"])
        lines.append(f"[{file_key}]")
        if overall:
            lines.append(f"  (overall) {overall[:200]}")
        for name, desc in per_sheet.items():
            lines.append(f"  {name}: {desc[:200]}")
    return "\n".join(lines)


def _rank_by_query(
    chunks: List[RetrievedChunk],
    question: str,
    kb: KnowledgeBase,
    top_k: int,
) -> List[RetrievedChunk]:
    """When router gives names directly, re-rank chunks by query similarity."""
    if not chunks:
        return []
    # Use the KB's own embedder to score each candidate against the query.
    query_vec = kb._embedder.embed(question)
    scored = []
    for rc in chunks:
        v = kb._embedder.embed(rc.chunk.metadata_text)
        score = _cosine(query_vec, v)
        scored.append(RetrievedChunk(
            chunk=rc.chunk, score=score,
            file_key=rc.file_key, sheet_names=rc.sheet_names,
        ))
    scored.sort(key=lambda r: r.score, reverse=True)
    return scored[:top_k]


def _cosine(a, b) -> float:
    import numpy as np
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) or 1e-9
    return float(np.dot(a, b) / denom)


def _answer_with_llm(
    question: str,
    chunks: List[RetrievedChunk],
    llm_client: LLMClient,
    route_reasoning: str,
) -> ChatAnswer:
    body = "\n\n".join(rc.to_markdown() for rc in chunks)
    user = f"""\
User question:
{question}

Retrieved sheet content:
{body}

Return the JSON object now."""
    try:
        raw = llm_client.complete(_ANSWER_SYSTEM, user, max_tokens=2048)
        parsed = json.loads(_extract_json_block(raw))
    except Exception as exc:
        logger.warning("Answer LLM failed: %s", exc)
        return ChatAnswer(
            question=question,
            answer=f"خطا در پاسخ‌دهی: {exc}",
            confidence="needs_review",
            route_reasoning=route_reasoning,
            sources=[_to_source(rc) for rc in chunks],
        )

    answer = str(parsed.get("answer", "") or "")
    confidence = str(parsed.get("confidence", "medium") or "medium")
    if confidence not in {"high", "medium", "needs_review"}:
        confidence = "medium"

    return ChatAnswer(
        question=question,
        answer=answer,
        confidence=confidence,
        route_reasoning=route_reasoning,
        sources=[_to_source(rc) for rc in chunks],
        raw_llm_response=raw,
    )


def _to_source(rc: RetrievedChunk) -> ChatSource:
    snippet = ""
    try:
        df = rc.chunk.dataframe
        for _, row in df.head(3).iterrows():
            text = " | ".join(str(v) for v in row.values if str(v) != "nan")
            if text.strip():
                snippet = text[:160]
                break
    except Exception:
        pass
    return ChatSource(
        file_key=rc.file_key,
        sheet_names=list(rc.sheet_names),
        chunk_id=rc.chunk.chunk_id,
        similarity=rc.score,
        snippet=snippet,
    )