"""
Splits and merges SheetData objects into logical Chunk objects.

Two real-world irregularities this handles, both flagged explicitly
by the domain expert:

1. One sheet can hold several unrelated tables stacked on top of each
   other (e.g. a "notes" sheet in a financial statement) - these are
   split into separate chunks, on blank-row boundaries, so an
   embedding search doesn't blur several tables' meaning together.
2. One logical table can be split across several sheets, because
   converting a PDF to Excel creates one sheet per PDF page and a
   table can span multiple pages - these are merged back into a
   single chunk so the full row/column structure survives.
"""

import re
from typing import List, Tuple

import pandas as pd

from ..models import SheetData
from .chunk_models import Chunk

# The exact fallback name pdf_to_excel_fa.py uses for a page whose title
# could not be guessed (see its `guess_title` / `process_page` functions).
# Deliberately narrow: generic Excel defaults like "Sheet1"/"Sheet2" must
# NOT match here, since those commonly label genuinely unrelated sheets.
_PDF_PAGE_FALLBACK_NAME_RE = re.compile(r"^صفحه[\s_]*\d+$")

Segment = Tuple[str, pd.DataFrame]  # (sheet_name, sub-table)


class TableSegmenter:
    """Builds the final list of Chunk objects for one file's sheets."""

    def build_chunks(self, file_key: str, sheets: List[SheetData]) -> List[Chunk]:
        segments: List[Segment] = []
        for sheet in sheets:
            segments.extend(self._split_blank_separated_blocks(sheet))

        merged = self._merge_page_continuations(segments)

        return [
            Chunk(chunk_id=f"{file_key}::chunk_{i}", file_key=file_key, source_sheet_names=names, dataframe=df)
            for i, (names, df) in enumerate(merged)
        ]

    # -- step 1: split one sheet into several tables ---------------------

    def _split_blank_separated_blocks(self, sheet: SheetData) -> List[Segment]:
        df = sheet.dataframe
        blank_mask = df.apply(self._is_blank_row, axis=1)

        blocks: List[List[int]] = []
        current_rows: List[int] = []
        for row_index, is_blank in zip(df.index, blank_mask):
            if is_blank:
                if current_rows:
                    blocks.append(current_rows)
                    current_rows = []
                continue
            current_rows.append(row_index)
        if current_rows:
            blocks.append(current_rows)

        if len(blocks) <= 1:
            return [(sheet.name, df)]
        return [(sheet.name, df.loc[rows].reset_index(drop=True)) for rows in blocks]

    @staticmethod
    def _is_blank_row(row: pd.Series) -> bool:
        return row.isna().all() or (row.astype(str).str.strip() == "").all()

    # -- step 2: merge sheets that are really one table's pages -----------

    def _merge_page_continuations(self, segments: List[Segment]) -> List[Segment]:
        if not segments:
            return []

        merged: List[Tuple[List[str], pd.DataFrame]] = [([segments[0][0]], segments[0][1])]
        for name, df in segments[1:]:
            prev_names, prev_df = merged[-1]
            if self._is_continuation(prev_names[-1], prev_df, name, df):
                merged[-1] = (prev_names + [name], pd.concat([prev_df, df], ignore_index=True))
            else:
                merged.append(([name], df))
        return merged

    @staticmethod
    def _is_continuation(prev_name: str, prev_df: pd.DataFrame, name: str, df: pd.DataFrame) -> bool:
        both_pdf_page_fallback = bool(_PDF_PAGE_FALLBACK_NAME_RE.match(prev_name.strip())) and bool(
            _PDF_PAGE_FALLBACK_NAME_RE.match(name.strip())
        )
        same_width = prev_df.shape[1] == df.shape[1]
        return both_pdf_page_fallback and same_width
