"""
llm_variable_resolver
======================
A pluggable pipeline that resolves checklist variables which the
deterministic matchers (exact / fuzzy / embedding) failed to find,
using file-aware LLM lookups.

Stage M1: merging checklist + operational JSON into a flat list of
pending VariableTask objects.

Stage M2: for each VariableTask, loading its source file (Excel,
Markdown, PDF, or an in-memory DataFrame) and resolving which
sheet(s)/section(s) are relevant, even when the sheet name does not
literally match the anchor.

Stage M3 (level-3 fallback): a small RAG pipeline - chunking,
embedding, and similarity search - for files where deterministic
sheet-name matching is unreliable or too costly (large, multi-sheet
files). See the `retrieval` sub-package.
Stage M3 (size router): decides per variable whether SheetResolver's
result is enough (level 1/2) or whether the file is large enough to
warrant semantic chunk retrieval (level 3, see `retrieval`), with a
confidence-margin gate so a close, possibly-wrong top match is never
silently trusted.
"""

from .content_locator import resolve_relevant_content
from .input_merger import InputMerger, build_variable_tasks
from .missing_value import DefaultMissingValueStrategy, MissingValueStrategy
from .models import RelevantSlice, SheetCandidate, SheetData, VariableTask
from .retrieval import ChunkRetriever
from .sheet_resolver import SheetResolver
from .size_router import SizeRouter
from .pipeline import ResolverPipeline
from .full_run import FullRunOrchestrator, FullRunReport, run_full_audit
from .knowledge_base import KnowledgeBase, IndexReport, RetrievedChunk
from .chatbot import ask as chat_ask, ChatAnswer, ChatSource

__all__ = [
    "VariableTask",
    "InputMerger",
    "build_variable_tasks",
    "MissingValueStrategy",
    "DefaultMissingValueStrategy",
    "SheetData",
    "SheetCandidate",
    "RelevantSlice",
    "SheetResolver",
    "resolve_relevant_content",
    "ChunkRetriever",
    "SizeRouter",
    "ResolverPipeline",
    "FullRunOrchestrator",
    "FullRunReport",
    "run_full_audit",
    "KnowledgeBase",
    "IndexReport",
    "RetrievedChunk",
    "chat_ask",
    "ChatAnswer",
    "ChatSource",
]
