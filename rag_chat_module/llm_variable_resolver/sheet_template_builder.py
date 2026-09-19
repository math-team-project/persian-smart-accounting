"""
Builds a lightweight, size-bounded structural view of every sheet in
a file: just its name and a sample of the non-numeric text labels
found in it (row labels, column headers - wherever they sit) - never
the full numeric grid.

This is what the future deep-audit stage (see the project's next
task) should hand an LLM that needs to re-examine a question after a
persistent mismatch: enough structural awareness of the WHOLE file to
find a better sheet/row/column - or confirm none exists - without
paying the token cost of sending every cell of a large workbook. It
is also useful today for building a short "what's in this file"
summary in extraction prompts when a question's variables might live
in more than one sheet.
"""

from typing import List

from .models import SheetData
from .retrieval.metadata_builder import collect_text_labels

_MAX_LABELS_PER_SHEET = 40


def build_sheet_templates(sheets: List[SheetData]) -> str:
    blocks = [_render_one_sheet(sheet) for sheet in sheets]
    return "\n".join(blocks)


def _render_one_sheet(sheet: SheetData) -> str:
    labels = collect_text_labels(sheet.dataframe)[:_MAX_LABELS_PER_SHEET]
    description = f" ({sheet.description})" if sheet.description else ""
    return f"- sheet: {sheet.name}{description}\n  labels: {', '.join(labels)}"
