"""
Domain models shared across the llm_variable_resolver package.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd


@dataclass(frozen=True)
class VariableTask:
    """A single variable whose value still needs to be resolved.

    This is the unit of work produced by the input-merging stage (M1)
    and consumed by the file-reading / LLM-resolution stages that come
    later in the pipeline.
    """

    question_id: str
    variable_name: str
    file_key: str
    sheet_anchor: str
    row_identifier: str
    column_identifier: str
    question_purpose: str
    current_raw_value: Optional[str] = None


@dataclass
class SheetData:
    """Canonical, format-agnostic representation of one sheet/section/table.

    Every reader (Excel, Markdown, PDF, in-memory DataFrame) must
    translate its source file into a list of SheetData objects so all
    downstream logic - sheet resolution, LLM prompting - never needs
    to know which original file format it came from.
    """

    name: str
    dataframe: pd.DataFrame
    description: Optional[str] = None


@dataclass
class SheetCandidate:
    """A sheet considered a possible match for a variable's anchor."""

    sheet: SheetData
    match_reason: str  # "name_match" | "description_match" | "content_scan" | "no_match"
    score: int = 0


@dataclass
class RelevantSlice:
    """The content handed off to the size router / LLM stage (M3/M4).

    `is_ambiguous=True` means the resolver could not settle on a
    single sheet on its own; `candidates_meta` documents why each
    included sheet was picked, for audit purposes.
    """

    content: str
    matched_sheet_names: List[str]
    is_ambiguous: bool
    candidates_meta: List[Dict[str, str]] = field(default_factory=list)
    size_estimate: int = 0
    resolution_method: str = ""  # "slice_prompt" | "full_file_prompt" | "chunk_retrieval"
