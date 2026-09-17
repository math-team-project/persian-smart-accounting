"""خواندن اسناد ورودی به شکل «شبکه‌ی سلولی» (مرحله‌ی ۱ خط پردازش بودجه).

ورودی این کارگاه دو سند است: اصلاحیه بودجه تفصیلی سال پایه و سال جاری. دو شکل
ورودی پشتیبانی می‌شود:

* **اکسل** (``.xlsx``/``.xls``) -- با ``openpyxl``/``xlrd`` خوانده می‌شود و
  سلول‌های ادغام‌شده با مقدار سلول بالای ادغام پر می‌شوند (دقیقاً همان رفتاری که
  تبدیل xls→xlsx موجود پروژه انجام می‌دهد)، چون در فرم‌های بودجه عنوان بخش‌ها
  معمولاً در سلول ادغام‌شده است.
* **PDF چاپ‌شده از اکسل** -- ابتدا تلاش برای استخراج جدول (``pdfplumber``) انجام
  می‌شود؛ اگر جدولی پیدا نشد، متن صفحه به‌صورت ردیفی خوانده می‌شود
  (``pdfplumber`` و سپس ``pymupdf``) و در نهایت اگر هیچ لایه‌ی متنی وجود نداشت،
  همان مسیر OCR موجود پروژه (``pdf2image`` + ``pytesseract``) به‌کار می‌آید.

نکته‌ی تجربی مهم: خروجی PDF چاپ‌شده از اکسل، حروف فارسی را در ترتیب معکوس
(آینه‌ای) می‌دهد در حالی که ارقام سالم می‌مانند. تصمیم درباره‌ی آینه‌ای‌بودن بر
پایه‌ی امتیاز واژگان دامنه و در سطح کل سند گرفته می‌شود (``decide_mirroring``)
و سلول‌های عددی هرگز معکوس نمی‌شوند.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from openpyxl.utils import get_column_letter

from budget_analysis.forms import FORM_KEYWORDS
from budget_analysis.text import clean_cell, decide_mirroring, is_blank, repair_mirrored

logger = logging.getLogger(__name__)

__all__ = [
    "ExtractionGridError",
    "GridBundle",
    "SheetGrid",
    "read_grids",
]

# سقف ستون‌های معقول برای یک فرم بودجه؛ ستون‌های خالی انتهایی حذف می‌شوند.
MAX_COLUMNS = 60

_SPLIT_RE = re.compile(r"\s{2,}|\t+")


@dataclass
class SheetGrid:
    """یک «شیت» منطقی: شبکه‌ای مستطیلی از سلول‌های نرمال‌شده‌ی متنی."""

    name: str
    rows: list[list[str]]
    row_offset: int = 1
    method: str = "xlsx"
    warnings: list[str] = field(default_factory=list)

    @property
    def width(self) -> int:
        return max((len(row) for row in self.rows), default=0)

    def text_sample(self, max_chars: int = 8000) -> str:
        """متن فشرده‌ی شیت برای تطبیق معنایی فرم (نام شیت + محتوای آن)."""
        parts: list[str] = []
        total = 0
        for row in self.rows:
            line = " ".join(cell for cell in row if cell)
            if not line:
                continue
            parts.append(line)
            total += len(line)
            if total >= max_chars:
                break
        return " ".join(parts)[:max_chars]

    def cell_ref(self, row_index: int, column_index: int) -> Optional[str]:
        """محل تقریبی داده -- «در صورت امکان» طبق اصل ۳ (ردیابی کامل نتایج)."""
        if self.method in ("xlsx", "xls"):
            return f"{get_column_letter(column_index + 1)}{self.row_offset + row_index}"
        return f"{self.name} / ردیف {row_index + 1} / ستون {column_index + 1}"


@dataclass
class GridBundle:
    """شبکه‌های استخراج‌شده از یک سند + نحوه‌ی استخراج و هشدارهای آن."""

    grids: list[SheetGrid]
    method: str
    warnings: list[str] = field(default_factory=list)
    mirrored_repaired: bool = False


# ---------------------------------------------------------------------------
# اکسل
# ---------------------------------------------------------------------------
def _pad_rows(rows: list[list[str]]) -> list[list[str]]:
    """حذف ستون‌های کاملاً خالی انتهایی و یکسان‌سازی طول ردیف‌ها."""
    while rows and all(is_blank(cell) for cell in rows[-1]):
        rows.pop()

    trimmed: list[list[str]] = []
    for row in rows:
        last = -1
        for index, cell in enumerate(row[:MAX_COLUMNS]):
            if not is_blank(cell):
                last = index
        trimmed.append(row[: last + 1])
    width = max((len(row) for row in trimmed), default=0)
    return [row + [""] * (width - len(row)) for row in trimmed]


def _read_xlsx(path: Path) -> list[SheetGrid]:
    from openpyxl import load_workbook

    workbook = load_workbook(str(path), data_only=True, read_only=False)
    grids: list[SheetGrid] = []
    try:
        for worksheet in workbook.worksheets:
            rows: list[list[str]] = []
            for row in worksheet.iter_rows():
                rows.append([clean_cell(cell.value) for cell in row])
            # پر کردن سلول‌های ادغام‌شده با مقدار سلول بالا-چپ ادغام (عنوان بخش‌ها
            # در فرم‌های بودجه معمولاً ادغام‌شده‌اند).
            for merged in worksheet.merged_cells.ranges:
                top_left = rows[merged.min_row - 1][merged.min_col - 1] if rows else ""
                if is_blank(top_left):
                    continue
                for row_index in range(merged.min_row - 1, min(merged.max_row, len(rows))):
                    target = rows[row_index]
                    for col_index in range(merged.min_col - 1, min(merged.max_col, len(target))):
                        if is_blank(target[col_index]):
                            target[col_index] = top_left
            rows = _pad_rows(rows)
            if not any(any(not is_blank(cell) for cell in row) for row in rows):
                continue
            grids.append(SheetGrid(name=worksheet.title or "Sheet", rows=rows, method="xlsx"))
    finally:
        workbook.close()
    return grids


def _read_xls(path: Path) -> list[SheetGrid]:
    import xlrd

    book = xlrd.open_workbook(str(path), formatting_info=False)
    grids: list[SheetGrid] = []
    for sheet in book.sheets():
        rows: list[list[str]] = [
            [clean_cell(sheet.cell_value(r, c)) for c in range(sheet.ncols)]
            for r in range(sheet.nrows)
        ]
        for row_lo, row_hi, col_lo, col_hi in getattr(sheet, "merged_cells", []):
            if not rows or row_lo >= len(rows):
                continue
            top_left = rows[row_lo][col_lo] if rows[row_lo] else ""
            if is_blank(top_left):
                continue
            for row_index in range(row_lo, min(row_hi, len(rows))):
                for col_index in range(col_lo, min(col_hi, len(rows[row_index]))):
                    if is_blank(rows[row_index][col_index]):
                        rows[row_index][col_index] = top_left
        rows = _pad_rows(rows)
        if not any(any(not is_blank(cell) for cell in row) for row in rows):
            continue
        grids.append(SheetGrid(name=sheet.name or "Sheet", rows=rows, method="xls"))
    return grids


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def _lines_to_grid(lines: Sequence[str]) -> list[list[str]]:
    """متن خطی (بدون ترسیم جدول) را با فاصله‌های دوکاراکتری به شبکه تبدیل می‌کند."""
    rows: list[list[str]] = []
    for line in lines:
        if not line or not line.strip():
            continue
        parts = [part.strip() for part in _SPLIT_RE.split(line.strip()) if part.strip()]
        if parts:
            rows.append(parts)
    return _pad_rows(rows)


def _pdf_tables_with_pdfplumber(path: Path) -> list[SheetGrid]:
    import pdfplumber

    grids: list[SheetGrid] = []
    with pdfplumber.open(str(path)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            for table_number, table in enumerate(page.extract_tables() or [], start=1):
                rows = [
                    [clean_cell(cell) for cell in row]
                    for row in table
                    if row is not None
                ]
                rows = _pad_rows(rows)
                if len(rows) < 2 or not any(any(not is_blank(c) for c in row) for row in rows):
                    continue
                grids.append(
                    SheetGrid(
                        name=f"صفحه {page_number} / جدول {table_number}",
                        rows=rows,
                        method="pdf_tables",
                    )
                )
    return grids


def _pdf_text_with_pdfplumber(path: Path) -> tuple[list[str], int]:
    import pdfplumber

    lines: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        page_count = len(pdf.pages)
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines.extend(text.splitlines())
    return lines, page_count


def _pdf_text_with_pymupdf(path: Path) -> list[str]:
    """جایگزین دوم متن: PyMuPDF ترتیب منطقی متن فارسی را بهتر بازسازی می‌کند."""
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - نسخه‌های قدیمی‌تر نام fitz دارند
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError:
            return []
    try:
        with pymupdf.open(str(path)) as document:
            return [line for page in document for line in (page.get_text("text") or "").splitlines()]
    except Exception:  # noqa: BLE001 - مسیر جایگزین است، خطا نباید کل کار را بخواباند
        logger.warning("استخراج متن PDF با PyMuPDF ناموفق بود.", exc_info=True)
        return []


def _resolve_tesseract() -> None:
    """مسیر Tesseract را مثل سایر بخش‌های پروژه از محیط/مسیر پیش‌فرض ویندوز می‌گیرد."""
    import pytesseract

    if getattr(pytesseract.pytesseract, "_psa_cmd_resolved", False):
        return
    default_windows = r"C:/Program Files/Tesseract-OCR/tesseract.exe"
    cmd = os.environ.get("TESSERACT_CMD") or (
        default_windows if os.path.exists(default_windows) else None
    )
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    pytesseract.pytesseract._psa_cmd_resolved = True  # type: ignore[attr-defined]


def _pdf_ocr_lines(path: Path) -> list[str]:
    """آخرین مسیر جایگزین: OCR صفحات PDF (همان الگوی موجود پروژه)."""
    import pytesseract
    from pdf2image import convert_from_path

    _resolve_tesseract()
    images = convert_from_path(str(path))
    lines: list[str] = []
    for image in images:
        text = pytesseract.image_to_string(image, lang="fas")
        lines.extend(text.splitlines())
    return lines


def _read_pdf(path: Path) -> GridBundle:
    warnings: list[str] = []

    grids = _pdf_tables_with_pdfplumber(path)
    if grids:
        return GridBundle(grids=grids, method="pdf_tables", warnings=warnings)

    warnings.append(
        "در این PDF جدول قابل‌تشخیصی یافت نشد؛ متن صفحه به‌صورت ردیفی خوانده می‌شود "
        "و ممکن است بخشی از ساختار سلولی از دست برود."
    )

    lines, _page_count = _pdf_text_with_pdfplumber(path)
    method = "pdf_text"
    if not [line for line in lines if line.strip()]:
        lines = _pdf_text_with_pymupdf(path)
        if [line for line in lines if line.strip()]:
            warnings.append("متن با pdfplumber استخراج نشد؛ از PyMuPDF استفاده شد.")

    if not [line for line in lines if line.strip()]:
        warnings.append("هیچ لایه‌ی متنی در PDF یافت نشد؛ پردازش با OCR انجام می‌شود.")
        try:
            lines = _pdf_ocr_lines(path)
            method = "pdf_ocr"
        except Exception as exc:  # noqa: BLE001
            raise ExtractionGridError(
                "هیچ متنی از فایل PDF استخراج نشد و OCR نیز در دسترس نبود. "
                "برای پردازش PDF اسکن‌شده باید Tesseract و Poppler نصب باشند."
            ) from exc
        if not [line for line in lines if line.strip()]:
            raise ExtractionGridError(
                "OCR هیچ متنی از فایل PDF برنگرداند. لطفاً از نسخه‌ی اکسل سند استفاده کنید "
                "یا کیفیت تصویر PDF را بهبود دهید."
            )

    rows = _lines_to_grid(lines)
    if not rows:
        raise ExtractionGridError("ساختار جدولی قابل‌استفاده‌ای در فایل PDF یافت نشد.")
    return GridBundle(
        grids=[SheetGrid(name="صفحه‌های سند", rows=rows, method=method)],
        method=method,
        warnings=warnings,
    )


class ExtractionGridError(Exception):
    """خطای خواندن سند در سطح شبکه (پیام آن فارسی و قابل‌نمایش به کاربر است)."""


# ---------------------------------------------------------------------------
# نقطه‌ی ورود
# ---------------------------------------------------------------------------
def _repair_mirroring(bundle: GridBundle) -> None:
    """در صورت آینه‌ای‌بودن کل سند، سلول‌های متنی را به ترتیب منطقی برمی‌گرداند."""
    texts = [cell for grid in bundle.grids for row in grid.rows for cell in row if cell]
    if not decide_mirroring(texts, FORM_KEYWORDS):
        return
    for grid in bundle.grids:
        grid.rows = [
            [repair_mirrored(cell, mirrored=True) for cell in row] for row in grid.rows
        ]
    bundle.mirrored_repaired = True
    bundle.warnings.append(
        "متن استخراج‌شده از PDF در ترتیب معکوس (آینه‌ای) بود و به‌صورت خودکار بازگردانده شد؛ "
        "ممکن است برخی واژه‌ها ناقص بازیابی شده باشند."
    )


def read_grids(path: Path) -> GridBundle:
    """سند را می‌خواند و شبکه‌های سلولی آن را برمی‌گرداند.

    Raises:
        ExtractionGridError: اگر پسوند پشتیبانی نشود یا سند خوانده/استخراج نشود.
    """
    path = Path(path)
    if not path.exists():
        raise ExtractionGridError(f"فایل سند یافت نشد: {path.name}")

    suffix = path.suffix.lower()
    try:
        if suffix == ".xlsx":
            grids = _read_xlsx(path)
        elif suffix == ".xls":
            grids = _read_xls(path)
        elif suffix == ".pdf":
            bundle = _read_pdf(path)
            _repair_mirroring(bundle)
            return bundle
        elif suffix in (".xlsm", ".xltx", ".xltm"):
            grids = _read_xlsx(path)
        else:
            raise ExtractionGridError(
                f"فرمت فایل «{path.name}» پشتیبانی نمی‌شود. فرمت‌های مجاز: xlsx، xls، pdf."
            )
    except ExtractionGridError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ExtractionGridError(f"خواندن فایل «{path.name}» ناموفق بود: {exc}") from exc

    bundle = GridBundle(grids=grids, method=suffix.lstrip("."), warnings=[])
    if not grids:
        raise ExtractionGridError(f"هیچ شیت قابل‌خواندنی در فایل «{path.name}» یافت نشد.")
    return bundle
