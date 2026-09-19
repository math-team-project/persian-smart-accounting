"""
Unified file readers: Excel, Markdown, DataFrame, PDF.

Every reader returns a list of SheetData objects, so downstream code
(sheet resolution, LLM prompting) never needs to know the original
file format. The PDF reader is a thin wrapper around the project's
existing Persian-aware PDF converters.
"""

from pathlib import Path
from typing import Union

from .base import BaseReader
from .dataframe_reader import DataFrameReader
from .excel_reader import ExcelReader
from .markdown_reader import MarkdownReader
from .pdf_reader import PDFReader


def get_reader_for(source: Union[str, Path]) -> BaseReader:
    """Pick a reader based on the source file's extension."""
    suffix = Path(source).suffix.lower()
    if suffix in {".xlsx", ".xls", ".xlsm", ".xltx", ".xltm"}:
        return ExcelReader()
    if suffix == ".pdf":
        return PDFReader()
    if suffix in {".md", ".markdown"}:
        return MarkdownReader()
    raise ValueError(
        f"Unsupported file type '{suffix}'. Supported: Excel (.xlsx/.xls), "
        f"PDF (.pdf), Markdown (.md)."
    )


__all__ = [
    "BaseReader",
    "ExcelReader",
    "MarkdownReader",
    "DataFrameReader",
    "PDFReader",
    "get_reader_for",
]