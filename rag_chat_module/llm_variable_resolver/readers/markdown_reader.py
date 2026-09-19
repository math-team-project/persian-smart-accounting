"""
Splits a markdown document into sections at heading boundaries and
parses each markdown table inside a section into its own SheetData.

Each SheetData's `name` is the nearest preceding heading (or a
synthetic "Section N" when there is none). This is what the
SheetResolver matches against the variable's `sheet_anchor`, so
headings should be descriptive in the source markdown.
"""

import re
from pathlib import Path
from typing import List, Union

import pandas as pd

from ..models import SheetData
from .base import BaseReader

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_SEPARATOR_ROW_RE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")


class MarkdownReader(BaseReader):
    def load(self, source: Union[str, Path]) -> List[SheetData]:
        text = Path(source).read_text(encoding="utf-8")
        return self._parse(text)

    def _parse(self, text: str) -> List[SheetData]:
        sheets: List[SheetData] = []
        current_heading = "Section 1"
        heading_counter = 1
        table_index = 0
        buffer: List[str] = []
        in_table = False

        def flush_table():
            nonlocal table_index, buffer, in_table
            if not in_table or not buffer:
                buffer = []
                in_table = False
                return
            table_index += 1
            df = self._buffer_to_df(buffer)
            if df is not None and not df.empty:
                sheets.append(SheetData(
                    name=f"{current_heading} (table {table_index})",
                    dataframe=df,
                ))
            buffer = []
            in_table = False

        for raw_line in text.splitlines():
            heading_match = _HEADING_RE.match(raw_line)
            if heading_match:
                flush_table()
                heading_counter += 1
                current_heading = (
                    heading_match.group(2).strip()
                    or f"Section {heading_counter}"
                )
                continue

            if _TABLE_ROW_RE.match(raw_line):
                buffer.append(raw_line)
                in_table = True
                continue

            if in_table:
                flush_table()

        flush_table()
        return sheets

    @staticmethod
    def _buffer_to_df(table_lines: List[str]) -> pd.DataFrame:
        """Convert markdown table lines to a DataFrame (headers stay as row 0)."""
        rows: List[List[str]] = []
        for line in table_lines:
            if _SEPARATOR_ROW_RE.match(line):
                continue  # drop the |---|---| separator
            stripped = line.strip().strip("|")
            cells = [c.strip() for c in stripped.split("|")]
            rows.append(cells)
        if not rows:
            return pd.DataFrame()

        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        return pd.DataFrame(rows)