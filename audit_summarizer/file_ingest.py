"""
file_ingest.py
----------------
Robust text extraction from audit-report source files.

Supported inputs:
    * .pdf                (text-layer PDFs; falls back gracefully if a page has no text)
    * .docx / .dotx        (native python-docx reading)
    * .doc / .rtf / .odt   (legacy formats -> converted to .docx via LibreOffice, then read)

Design notes:
    * Extraction never silently returns an empty string: if nothing could be
      extracted, an `ExtractionError` is raised with a clear, actionable message.
    * PDF text is extracted with PyMuPDF (fitz) first, because it reconstructs
      correct *logical* reading order for right-to-left scripts (Persian/Arabic).
      pdfplumber and pypdf sort characters by raw x-coordinate, which for RTL
      text produces fully mirrored/reversed lines (e.g. "بسمه تعالی" comes out
      as "»يلاعت همسب«") - this is a well-known limitation, not a one-off bug.
    * As a safety net (in case a given PDF's embedded font still confuses the
      extractor), a heuristic pass detects lines that still look reversed
      (based on common Persian function words) and un-reverses them.
    * Tables are extracted too (common in audit reports: budget-vs-actual tables,
      findings tables, etc.) and are appended after the running text, clearly
      marked, so the LLM prompt in llm_client.py can still see the numbers.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pytesseract
from pdf2image import convert_from_path
import os

# مسیر نصب Tesseract
pytesseract.pytesseract.tesseract_cmd = r'C:/Program Files/Tesseract-OCR/tesseract.exe'


class ExtractionError(Exception):
    """Raised when text could not be reliably extracted from the input file."""


@dataclass
class ExtractedDocument:
    """Container for everything pulled out of the source file."""

    source_path: Path
    text: str
    tables: list[list[list[str]]] = field(default_factory=list)
    page_count: int | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip() and not self.tables

    def as_prompt_text(self, max_chars: int | None = None) -> str:
        """Flatten text + tables into one block suitable for an LLM prompt."""
        parts = [self.text.strip()]
        for i, table in enumerate(self.tables, start=1):
            rows = "\n".join(" | ".join(cell or "" for cell in row) for row in table)
            parts.append(f"\n[جدول {i} / Table {i}]\n{rows}")
        combined = "\n\n".join(p for p in parts if p.strip())
        if max_chars and len(combined) > max_chars:
            combined = combined[:max_chars] + "\n\n[...متن به دلیل محدودیت طول کوتاه شد / truncated...]"
        return combined


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# RTL mirrored-text safety net
# --------------------------------------------------------------------------- #

# Common short Persian function words. If a line contains several of these
# only *after* being reversed, the line was almost certainly extracted in
# mirrored (visual) order instead of logical (reading) order.
_PERSIAN_FUNCTION_WORDS = {
    "است", "و", "به", "از", "در", "که", "را", "این", "برای", "با", "یا",
    "شده", "می‌شود", "بر", "تا", "آن", "هر", "نیز", "اما", "یک", "خود",
}

_WORD_SPLIT_RE = re.compile(r"\s+")
_PUNCT_STRIP = ".،:؛!؟»«()[]{}\"'"


def _persian_word_score(line: str) -> int:
    words = _WORD_SPLIT_RE.split(line.strip())
    return sum(1 for w in words if w.strip(_PUNCT_STRIP) in _PERSIAN_FUNCTION_WORDS)


def _fix_reversed_rtl_lines(text: str) -> str:
    """
    Per line, compare the "Persian function word" score of the line as-is vs.
    the same line fully character-reversed. If reversing clearly scores
    better, assume the line was extracted in mirrored order and un-reverse it.
    Lines with too few words to judge are left untouched.
    """
    fixed_lines = []
    for line in text.splitlines():
        if len(line.strip()) < 4:
            fixed_lines.append(line)
            continue
        normal_score = _persian_word_score(line)
        reversed_line = line[::-1]
        reversed_score = _persian_word_score(reversed_line)
        if reversed_score > normal_score and reversed_score >= 2:
            fixed_lines.append(reversed_line)
        else:
            fixed_lines.append(line)
    return "\n".join(fixed_lines)


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #

def _extract_pdf_pymupdf(path: Path) -> tuple[str, int] | None:
    """Primary extractor: PyMuPDF gives correct logical reading order for RTL text."""
    try:
        import pymupdf  # aka fitz
    except ImportError:
        return None

    try:
        with pymupdf.open(str(path)) as doc:
            page_count = doc.page_count
            text_chunks = [page.get_text("text") or "" for page in doc]
    except Exception:
        return None

    full_text = "\n\n".join(text_chunks)
    return full_text, page_count


def _extract_pdf(path: Path) -> ExtractedDocument:
    warnings: list[str] = []
    tables: list[list[list[str]]] = []
    full_text = ""
    page_count = 0

    pymupdf_result = _extract_pdf_pymupdf(path)
    if pymupdf_result is not None:
        full_text, page_count = pymupdf_result
        if not full_text.strip():
            warnings.append("استخراج با PyMuPDF متنی برنگرداند؛ در حال تلاش با pdfplumber...")
    else:
        warnings.append("PyMuPDF در دسترس نیست (pip install pymupdf)؛ استفاده از pdfplumber به‌عنوان جایگزین.")

    # pdfplumber fallback: also always run it for *tables* (PyMuPDF's table
    # detection is less reliable for the loosely-bordered tables common in
    # audit reports), and as a text fallback if PyMuPDF found nothing.
    try:
        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            page_count = page_count or len(pdf.pages)
            plumber_text_chunks = []
            for idx, page in enumerate(pdf.pages, start=1):
                if not full_text.strip():
                    page_text = page.extract_text() or ""
                    if not page_text.strip():
                        warnings.append(f"صفحه {idx} فاقد لایه متنی قابل استخراج بود (احتمالاً اسکن‌شده).")
                    else:
                        plumber_text_chunks.append(page_text)
                for table in page.extract_tables() or []:
                    tables.append(table)
            if not full_text.strip():
                full_text = "\n\n".join(plumber_text_chunks)
    except ImportError:
        pass
    except Exception as e:
        if not full_text.strip():
            raise ExtractionError(f"خطا در خواندن فایل PDF: {e}") from e

    # Last-resort fallback: pypdf.
    if not full_text.strip():
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            page_count = len(reader.pages)
            full_text = "\n\n".join((p.extract_text() or "") for p in reader.pages)
        except Exception:
            pass

    if not full_text.strip() and not tables:
        raise ExtractionError(
            "هیچ متنی از فایل PDF استخراج نشد. این فایل احتمالاً یک اسکن تصویری است "
            "و نیاز به OCR دارد (این سرویس فعلاً OCR انجام نمی‌دهد)."
        )

    # Safety net: fix any lines that still came out mirrored/reversed
    # (mainly relevant to the pdfplumber/pypdf fallback paths).
    full_text = _fix_reversed_rtl_lines(full_text)

    return ExtractedDocument(
        source_path=path, text=full_text, tables=tables,
        page_count=page_count, warnings=warnings,
    )

# --------------------------------------------------------------------------- #
# PDF OCR
# --------------------------------------------------------------------------- #
def _pdf_to_text(pdf_path: str) -> str:
    """
    تبدیل PDF فارسی به متن با استفاده از OCR

    Args:
        pdf_path (str): مسیر فایل PDF

    Returns:
        str: متن استخراج‌شده از تمام صفحات PDF
    """

    extracted_text = ""

    # تبدیل صفحات PDF به تصویر
    images = convert_from_path(pdf_path)

    # OCR روی هر صفحه
    for page_number, image in enumerate(images, start=1):
        text = pytesseract.image_to_string(
            image,
            lang="fas"
        )

        extracted_text += text

        # جدا کردن صفحات از یکدیگر
        extracted_text += "\n\n"

    return extracted_text


# --------------------------------------------------------------------------- #
# DOCX (and, after conversion, legacy DOC/RTF/ODT)
# --------------------------------------------------------------------------- #

def _extract_docx(path: Path) -> ExtractedDocument:
    try:
        import docx  # python-docx
    except ImportError as e:  # pragma: no cover
        raise ExtractionError(
            "python-docx is not installed. Run: pip install python-docx"
        ) from e

    try:
        document = docx.Document(str(path))
    except Exception as e:
        raise ExtractionError(f"خطا در خواندن فایل Word: {e}") from e

    paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
    tables: list[list[list[str]]] = []
    for table in document.tables:
        rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        if rows:
            tables.append(rows)

    full_text = "\n".join(paragraphs)

    if not full_text.strip() and not tables:
        raise ExtractionError("فایل Word خالی به نظر می‌رسد یا متنی در آن یافت نشد.")

    return ExtractedDocument(source_path=path, text=full_text, tables=tables)


def _convert_legacy_to_docx(path: Path, workdir: Path) -> Path:
    """Use headless LibreOffice to turn .doc/.rtf/.odt into .docx."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise ExtractionError(
            "برای پردازش فایل‌های .doc قدیمی، LibreOffice (soffice) لازم است اما "
            "روی سیستم یافت نشد. لطفاً LibreOffice را نصب کنید یا فایل را به .docx تبدیل کنید."
        )
    cmd = [soffice, "--headless", "--convert-to", "docx", "--outdir", str(workdir), str(path)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as e:
        raise ExtractionError("تبدیل فایل با LibreOffice بیش از حد طول کشید (timeout).") from e

    if result.returncode != 0:
        raise ExtractionError(f"تبدیل فایل با LibreOffice ناموفق بود: {result.stderr.strip()}")

    converted = workdir / (path.stem + ".docx")
    if not converted.exists():
        raise ExtractionError("تبدیل فایل انجام شد اما فایل خروجی .docx یافت نشد.")
    return converted


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def extract_text(file_path: str | Path) -> ExtractedDocument:
    """
    Extract text (+ tables) from a PDF or Word document.

    Raises:
        FileNotFoundError: if the path does not exist.
        ExtractionError: if the file type is unsupported or extraction fails.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"فایل یافت نشد: {path}")

    suffix = path.suffix.lower()

    if suffix == ".pdf":
        # return _extract_pdf(path)

        dir_path = os.path.dirname(path)
        ext = os.path.splitext(path)[1]
        new_filename = "Report"
        new_path = os.path.join(dir_path, new_filename + ext)
        os.rename(path, new_path)
        ocr_text = _pdf_to_text(new_path)
        if not ocr_text.strip():
            raise ExtractionError(
                "هیچ متنی از فایل PDF استخراج نشد (OCR متن خالی برگرداند)."
            )
        return ExtractedDocument(source_path=Path(new_path), text=ocr_text)

    if suffix == ".docx" or suffix == ".dotx":
        return _extract_docx(path)

    if suffix in {".doc", ".rtf", ".odt"}:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            converted = _convert_legacy_to_docx(path, tmp_path)
            extracted = _extract_docx(converted)
            # Re-point source_path to the original file for reporting purposes.
            extracted.source_path = path
            return extracted

    raise ExtractionError(
        f"فرمت فایل پشتیبانی نمی‌شود: '{suffix}'. فرمت‌های مجاز: .pdf, .doc, .docx, .rtf, .odt"
    )
