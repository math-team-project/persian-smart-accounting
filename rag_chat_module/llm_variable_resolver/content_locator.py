"""
Public entry point for stages M2 + M3.

Given one VariableTask and a registry mapping each logical file key
(the "file" field from the checklist JSON) to either a file path or
in-memory data, this loads the right file and routes the variable to
the cheapest resolution strategy that is likely to work (see
SizeRouter): a direct sheet match, the whole file, or - only for
large files - semantic chunk retrieval.

Important: pass the SAME `router` instance across every VariableTask
in one batch/run. SizeRouter holds a ChunkRetriever internally, which
caches each file's chunk index - reusing one router instance means a
file already indexed for one variable is not re-indexed for the next
variable that points at the same file.
"""

from typing import Callable, Dict, Optional, Union

import pandas as pd

from .models import RelevantSlice, VariableTask
from .readers import DataFrameReader, get_reader_for
from .size_router import SizeRouter

# A file key can point to a path on disk, or to data already in memory.
FileSource = Union[str, pd.DataFrame, Dict[str, pd.DataFrame]]
FileRegistry = Dict[str, FileSource]

# file_key -> {sheet_name: manual one-line description}, supplied by the user.
SheetDescriptions = Dict[str, Dict[str, str]]

def load_sheets(source: FileSource):
    if isinstance(source, (pd.DataFrame, dict)):
        return DataFrameReader().load(source)    
    return get_reader_for(source).load(source)
    
def resolve_relevant_content(
    task: VariableTask,
    file_registry: FileRegistry,
    router: SizeRouter,
    sheet_descriptions: Optional[SheetDescriptions] = None,
) -> RelevantSlice:
    source = file_registry.get(task.file_key)
    if source is None:
        return RelevantSlice(
            content="",
            matched_sheet_names=[],
            is_ambiguous=True,
            candidates_meta=[{"error": f"Unknown file key: '{task.file_key}'"}],
        )

    sheets = load_sheets(source)
    _attach_descriptions(sheets, (sheet_descriptions or {}).get(task.file_key, {}))

    return router.resolve(task, sheets)


def _attach_descriptions(sheets, descriptions: Dict[str, str]) -> None:
    for sheet in sheets:
        if sheet.name in descriptions:
            sheet.description = descriptions[sheet.name]
