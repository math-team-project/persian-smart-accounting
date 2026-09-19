"""
PDF reader.

Strategy (delegating rather than reimplementing):
  1. Convert the PDF to a temporary xlsx using the project's existing
     Persian-aware converter (pdf_to_excel_fa.convert). That converter
     handles the two hard parts of Persian PDFs - mirrored reading
     order and raw presentation-form glyphs - and preserves table
     structure, which is what downstream sheet resolution needs.
  2. Load the resulting xlsx with ExcelReader.

If step 1 fails or produces only an empty placeholder, fall back to
file_to_text.extract_text for a plain-text representation and expose
the whole document as one SheetData, so the pipeline can still
proceed (with reduced structure).
"""

import tempfile
from pathlib import Path
from typing import List, Union

import pandas as pd

from ..models import SheetData
from .base import BaseReader
from .excel_reader import ExcelReader
from . import pdf_to_excel_fa
from . import file_to_text


class PDFReader(BaseReader):
    def __init__(self, use_text_fallback: bool = True):
        self._use_text_fallback = use_text_fallback
        self._excel = ExcelReader()

    def load(self, source: Union[str, Path]) -> List[SheetData]:
        path = Path(source)
        primary_error = None

        # Primary path: pdf -> xlsx (table-aware, Persian-aware).
        try:
            with tempfile.TemporaryDirectory() as tmp:
                xlsx_path = Path(tmp) / "converted.xlsx"
                pdf_to_excel_fa.convert(str(path), str(xlsx_path))
                sheets = self._excel.load(xlsx_path)
                if _has_real_data(sheets):
                    return sheets
        except Exception as exc:
            primary_error = exc

        if not self._use_text_fallback:
            raise RuntimeError(
                f"PDF conversion produced no usable sheets and text fallback "
                f"is disabled (primary error: {primary_error})"
            )

        # Fallback: plain text (may use OCR under the hood).
        try:
            doc = file_to_text.extract_text(path)
        except Exception as exc:
            raise RuntimeError(
                f"Both pdf_to_excel_fa and file_to_text failed. "
                f"Primary: {primary_error}; fallback: {exc}"
            ) from exc

        return [SheetData(name=path.stem or "PDF", dataframe=_text_to_df(doc.text))]


def _has_real_data(sheets: List[SheetData]) -> bool:
    """True if any sheet carries non-placeholder content.

    pdf_to_excel_fa writes a single 'هیچ محتوایی از PDF استخراج نشد.'
    cell when it cannot find anything; we treat that as empty so the
    text fallback still runs.
    """
    for sheet in sheets:
        df = sheet.dataframe
        if df is None or df.empty:
            continue
        values = [
            str(v).strip()
            for v in df.astype(str).values.flatten()
            if str(v).strip() and str(v).strip().lower() != "nan"
        ]
        if not values:
            continue
        if len(values) == 1 and "هیچ محتوایی" in values[0]:
            continue
        return True
    return False


def _text_to_df(text: str) -> pd.DataFrame:
    """One-column DataFrame, one line per row."""
    lines = (text or "").splitlines()
    if not lines:
        return pd.DataFrame()
    return pd.DataFrame({0: lines})