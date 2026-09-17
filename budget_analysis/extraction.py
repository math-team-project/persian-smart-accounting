"""مرحله‌ی ۱ خط پردازش: استخراج ساختارمند از اسناد بودجه (کد، بدون مدل زبانی).

خروجی این مرحله یک ``ExtractionBundle`` است: برای هر سند، فهرست فرم‌های
شناسایی‌شده و برای هر فرم، فهرست سلول‌های عددی با محل معنایی کامل
(فرم ← بخش ← ردیف ← ستون ← سال) و محل فیزیکی تقریبی (مرجع سلول). همین ساختار
هم ورودی مرحله‌ی تحلیل است و هم داده‌ی ردیابی یافته‌های گزارش.

دو اصل پرامپت مرجع که این‌جا رعایت می‌شوند:

* **عدم حدس**: اگر فرمی شناسایی نشود، در ``missing_form_keys`` می‌آید؛ اگر ردیفی
  عنوان قابل‌اعتماد نداشته باشد، اصلاً سلولی تولید نمی‌شود. هیچ مقدار یا عنوانی
  ساخته نمی‌شود.
* **شناسایی معنایی بر موقعیت**: تطبیق فرم‌ها بر پایه‌ی محتوا انجام می‌شود و
  شماره‌ی ردیف اکسل فقط به‌عنوان «محل تقریبی» (و آن هم در صورت وجود) نگه داشته
  می‌شود.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Sequence

from budget_analysis.config import BudgetConfig
from budget_analysis.forms import FormMatch, expected_form_keys, form_name, match_forms
from budget_analysis.grid import GridBundle, SheetGrid, read_grids
from budget_analysis.schemas import (
    DocumentExtraction,
    ExtractedCell,
    ExtractionBundle,
    FormExtraction,
)
from budget_analysis.text import (
    clean_cell,
    detect_unit,
    detect_years,
    dominant_year,
    is_blank,
    is_mostly_numeric,
    match_ratio,
    parse_number,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DocumentSlot",
    "extract_bundle",
    "extract_document",
]

# سقف سلول‌های نگه‌داشته‌شده از هر سند (فایل‌های بودجه‌ی بزرگ) -- در صورت عبور،
# هشدار ثبت می‌شود تا در گزارش دیده شود و سکوت‌کردن باعث گم‌شدن داده نشود.
MAX_CELLS_PER_DOCUMENT = 6000
# حداکثر تعداد ردیف ابتدایی که به‌عنوان ناحیه‌ی سرصفحه بررسی می‌شود.
HEADER_SCAN_ROWS = 15
# حداقل امتیاز شباهت برای «جمع‌بودن» عنوان یک ردیف (برای محاسبه‌ی لنگرهای عددی).
TOTAL_ROW_CUTOFF = 88.0

TOTAL_ROW_MARKERS = ("جمع", "جمع کل", "جمع منابع", "جمع مصارف", "مجموع")
ROW_NUMBER_COLUMN_MARKERS = ("ردیف", "شماره ردیف", "شماره")
HEADER_MARKERS = (
    "عنوان",
    "شرح",
    "عنوان قلم",
    "شرح اقلام",
    "ردیف",
    "مبلغ",
    "مبالغ",
    "جمع",
    "عملکرد",
    "بودجه",
    "اصلاحیه",
    "سال",
    "واحد",
    "درصد",
    "تعداد",
)

DocumentSlot = str  # "base_year" | "current_year"

_SLOT_ROLE_FA = {"base_year": "سال پایه", "current_year": "سال جاری"}


# ---------------------------------------------------------------------------
# تشخیص سرصفحه، ستون‌ها و ردیف‌ها
# ---------------------------------------------------------------------------
def _looks_like_header_row(row: Sequence[str]) -> bool:
    non_blank = [cell for cell in row if not is_blank(cell)]
    if not non_blank:
        return False
    numeric = sum(1 for cell in non_blank if is_mostly_numeric(cell))
    textual = [cell for cell in non_blank if not is_mostly_numeric(cell)]
    if not textual:
        return False
    # سرصفحه: غلبه‌ی متن بر عدد و حضور یک واژه‌ی سرصفحه‌ای شناخته‌شده.
    if numeric / len(non_blank) > 0.34:
        return False
    return any(
        match_ratio(marker, cell) >= 82.0 for marker in HEADER_MARKERS for cell in textual
    )


def _detect_header_block(rows: Sequence[Sequence[str]]) -> tuple[int, int]:
    """بازه‌ی ردیف‌های سرصفحه: ``(شروع, پایان)`` با اندیس صفرمبنا (پایان انحصاری)."""
    start = -1
    for index, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        if _looks_like_header_row(row):
            start = index
            break
    if start < 0:
        return 0, 0

    end = start + 1
    for index in range(start + 1, min(len(rows), HEADER_SCAN_ROWS)):
        row = rows[index]
        if _looks_like_header_row(row):
            end = index + 1
            continue
        # ردیفی که شماره + مقدار دارد، دیگر سرصفحه نیست.
        if any(parse_number(cell) is not None for cell in row):
            break
        if not any(not is_blank(cell) for cell in row):
            end = index + 1
            continue
        break
    return start, end


def _build_column_titles(rows: Sequence[Sequence[str]], header_block: tuple[int, int]) -> list[str]:
    """عنوان هر ستون را از ردیف‌های سرصفحه می‌سازد.

    ردیف‌هایی که فقط یک سلول پرمحتوا دارند (مثل عنوان بالای فرم) سرصفحه‌ی ستون
    نیستند و در ساخت عنوان ستون شرکت نمی‌کنند؛ وگرنه عنوان آن‌ها به ستون اول
    می‌چسبد و محل داده را نامفهوم می‌کند.
    """
    start, end = header_block
    if start == end:
        return []
    width = max((len(row) for row in rows), default=0)
    header_rows = [
        rows[index]
        for index in range(start, end)
        if sum(1 for cell in rows[index] if not is_blank(cell)) >= 2
    ]
    titles: list[str] = []
    for column in range(width):
        parts: list[str] = []
        for row in header_rows:
            cell = clean_cell(row[column]) if column < len(row) else ""
            if is_blank(cell) or is_mostly_numeric(cell):
                continue
            cell = cell[:80]
            if cell not in parts:
                parts.append(cell)
        titles.append(" ".join(parts))
    return titles


def _is_row_number_column(title: str) -> bool:
    return any(match_ratio(marker, title) >= 88.0 for marker in ROW_NUMBER_COLUMN_MARKERS)


def _is_total_row(title: str) -> bool:
    return any(match_ratio(marker, title) >= TOTAL_ROW_CUTOFF for marker in TOTAL_ROW_MARKERS)


def _extract_cells_from_grid(
    grid: SheetGrid,
    match: FormMatch,
    unit: Optional[str],
    *,
    document_year: Optional[str],
) -> tuple[list[ExtractedCell], dict[str, float], list[str], list[str]]:
    """سلول‌های عددی یک فرم را از شبکه‌ی شیت استخراج می‌کند.

    Returns:
        ``(cells, anchors, section_titles, row_titles)``
    """
    rows = grid.rows
    header_block = _detect_header_block(rows)
    column_titles = _build_column_titles(rows, header_block)
    data_start = header_block[1]

    cells: list[ExtractedCell] = []
    anchors: dict[str, float] = {}
    sections: list[str] = []
    row_titles: list[str] = []
    current_section: Optional[str] = None
    confidence_base = max(0.2, match.score / 100.0) * (0.65 if grid.method.startswith("pdf") else 1.0)

    for row_index in range(data_start, len(rows)):
        row = rows[row_index]
        non_blank = [(index, clean_cell(cell)) for index, cell in enumerate(row) if not is_blank(cell)]
        if not non_blank:
            continue

        text_cells = [(index, cell) for index, cell in non_blank if not is_mostly_numeric(cell)]
        numeric_cells = [
            (index, cell, parse_number(cell)) for index, cell in non_blank if is_mostly_numeric(cell)
        ]
        numeric_cells = [(index, cell, value) for index, cell, value in numeric_cells if value is not None]

        if not text_cells:
            # ردیف بدون هیچ عنوان متنی -- طبق اصل «عدم حدس» رها می‌شود (ردیف بی‌نام
            # قابل ردیابی نیست و نمی‌توان آن را به گزارش نسبت داد).
            continue

        if len(non_blank) == 1 and not numeric_cells:
            # ردیف فقط‌متنی = عنوان بخش (در فرم‌های بودجه بخش‌ها بدون مقدار می‌آیند).
            current_section = text_cells[0][1]
            if current_section not in sections:
                sections.append(current_section)
            continue

        # عنوان ردیف: طولانی‌ترین سلول متنی که خودش «شماره ردیف» نیست.
        title_candidates = [
            (index, cell) for index, cell in text_cells if not _is_row_number_column(column_titles[index] if index < len(column_titles) else "")
        ]
        if not title_candidates:
            title_candidates = text_cells
        row_title = max(title_candidates, key=lambda item: len(item[1]))[1]
        if not row_title:
            continue

        row_number: Optional[int] = None
        for index, _cell, value in numeric_cells:
            title = column_titles[index] if index < len(column_titles) else ""
            if _is_row_number_column(title) or index == 0:
                if float(value).is_integer() and 0 <= value < 100_000:
                    row_number = int(value)
                    break

        if row_title not in row_titles:
            row_titles.append(row_title)

        for index, cell, value in numeric_cells:
            column_title = column_titles[index] if index < len(column_titles) else ""
            column_title = column_title or f"ستون {index + 1}"
            # ستون «ردیف» مقدار داده‌ای نیست.
            if _is_row_number_column(column_title):
                continue
            if row_number is not None and value == row_number and index == 0:
                continue

            column_year = _first_year(column_title)
            cell_ref = grid.cell_ref(row_index, index)
            cells.append(
                ExtractedCell(
                    form_key=match.form_key,
                    form_name=match.form_name,
                    section=current_section,
                    row_title=row_title,
                    row_number=row_number,
                    column_title=column_title,
                    column_index=index,
                    value=float(value),
                    raw_value=cell,
                    unit=unit,
                    year=column_year or document_year,
                    cell_ref=cell_ref,
                    confidence=round(confidence_base, 3),
                    source_document="",
                )
            )

        if _is_total_row(row_title):
            for index, _cell, value in numeric_cells:
                column_title = column_titles[index] if index < len(column_titles) else f"ستون {index + 1}"
                if _is_row_number_column(column_title):
                    continue
                key = f"{match.form_name} | {row_title} | {column_title}"
                anchors[key] = float(value)

    return cells, anchors, sections, row_titles


def _first_year(text: str) -> Optional[str]:
    years = detect_years(text)
    return years[0] if years else None


def _column_titles_from_grid(grid: SheetGrid) -> list[str]:
    return [title for title in _build_column_titles(grid.rows, _detect_header_block(grid.rows)) if title]


# ---------------------------------------------------------------------------
# استخراج یک سند
# ---------------------------------------------------------------------------
def extract_document(
    path: Path,
    slot: DocumentSlot,
    *,
    filename: Optional[str] = None,
    config: Optional[BudgetConfig] = None,
) -> DocumentExtraction:
    """یک سند (سال پایه یا سال جاری) را به ساختار میانی تبدیل می‌کند."""
    config = config or BudgetConfig()
    path = Path(path)
    bundle: GridBundle = read_grids(path)

    sheets_text: list[tuple[str, str]] = [(grid.name, grid.text_sample()) for grid in bundle.grids]
    matches, unmatched = match_forms(sheets_text)

    whole_text = " ".join(sample for _name, sample in sheets_text)
    document_year = dominant_year(whole_text)
    unit = detect_unit(whole_text)

    warnings = list(bundle.warnings)
    if not matches:
        warnings.append(
            "هیچ‌یک از فرم‌های شناخته‌شده‌ی بودجه در این سند شناسایی نشد؛ "
            "تحلیل ابعادی این سند ممکن نخواهد بود."
        )
    if unit is None:
        warnings.append(
            "واحد مبالغ در سند به‌صراحت ذکر نشده است؛ در گزارش «واحد: نامشخص» درج می‌شود."
        )
    if document_year is None:
        warnings.append("سال سند به‌صورت خودکار شناسایی نشد؛ سال باید از ورودی تعیین شود.")

    cells: list[ExtractedCell] = []
    anchors: dict[str, float] = {}
    forms: list[FormExtraction] = []
    truncated = False

    for match in matches:
        grid = bundle.grids[match.sheet_index]
        grid_cells, grid_anchors, sections, row_titles = _extract_cells_from_grid(
            grid,
            match,
            unit,
            document_year=document_year,
        )
        for cell in grid_cells:
            cell.source_document = slot
        cells.extend(grid_cells)
        anchors.update(grid_anchors)
        forms.append(
            FormExtraction(
                form_key=match.form_key,
                form_name=match.form_name,
                sheet_name=match.sheet_name,
                confidence=round(match.score / 100.0, 3),
                matched_terms=list(match.matched_terms),
                section_titles=sections[:50],
                row_titles=row_titles[:200],
                column_titles=_column_titles_from_grid(grid)[:40],
                cell_count=len(grid_cells),
            )
        )

    if len(cells) > MAX_CELLS_PER_DOCUMENT:
        warnings.append(
            f"تعداد اقلام عددی این سند ({len(cells)}) از سقف پردازش ({MAX_CELLS_PER_DOCUMENT}) "
            "بیشتر است؛ اقلام انتهایی در تحلیل لحاظ نشده‌اند."
        )
        cells = cells[:MAX_CELLS_PER_DOCUMENT]
        truncated = True

    matched_keys = {match.form_key for match in matches}
    missing_keys = [key for key in expected_form_keys() if key not in matched_keys]

    unsupported = [
        bundle.grids[index].name
        for index in unmatched
        if bundle.grids[index].name not in {match.sheet_name for match in matches}
    ]

    return DocumentExtraction(
        slot=slot,  # type: ignore[arg-type]
        role_fa=_SLOT_ROLE_FA.get(slot, slot),
        filename=filename or path.name,
        detected_year=document_year,
        unit=unit,
        extraction_method=bundle.method,
        mirrored_text_repaired=bundle.mirrored_repaired,
        forms=forms,
        missing_form_keys=missing_keys,
        unsupported_sheets=unsupported[:20],
        warnings=warnings,
        cells=cells,
        anchors=dict(list(anchors.items())[:300]),
        cell_limit_reached=truncated,
    )


def extract_bundle(
    paths: dict[str, Path],
    *,
    config: Optional[BudgetConfig] = None,
    filenames: Optional[dict[str, str]] = None,
) -> ExtractionBundle:
    """هر دو سند را استخراج و سال‌های نهایی تحلیل را تعیین می‌کند.

    سال‌ها به این ترتیب تعیین می‌شوند: مقدار صریح ورودی کاربر ← سال تشخیص‌داده‌شده
    از خود سند. هیچ سالی در کد hard-code نشده است؛ اگر هیچ‌کدام تعیین نشود، در
    گزارش «در اسناد موجود نیست» درج می‌شود.
    """
    config = config or BudgetConfig()
    filenames = filenames or {}
    documents: list[DocumentExtraction] = []
    warnings: list[str] = []

    for slot in ("base_year", "current_year"):
        path = paths.get(slot)
        if path is None:
            warnings.append(f"سند «{_SLOT_ROLE_FA[slot]}» بارگذاری نشده است.")
            continue
        documents.append(
            extract_document(path, slot, filename=filenames.get(slot), config=config)
        )

    base_document = next((doc for doc in documents if doc.slot == "base_year"), None)
    current_document = next((doc for doc in documents if doc.slot == "current_year"), None)

    base_year = config.base_year or (base_document.detected_year if base_document else None)
    current_year = config.current_year or (current_document.detected_year if current_document else None)

    if base_year and current_year:
        if base_year == current_year:
            warnings.append(
                f"سال تشخیص‌داده‌شده برای هر دو سند یکسان است ({base_year})؛ "
                "مقایسه‌ی بین‌سالانه معتبر نخواهد بود مگر با تعیین دستی سال‌ها."
            )
        elif base_year > current_year:
            warnings.append(
                f"سال سند مبنا ({base_year}) بزرگ‌تر از سال سند جاری ({current_year}) است؛ "
                "لطفاً صحت تخصیص اسناد را بررسی کنید."
            )
    elif not base_year or not current_year:
        warnings.append(
            "سال پایه یا سال جاری تعیین نشد؛ برای مقایسه‌های بین‌سالانه باید سال‌ها "
            "وارد شوند یا در سند ذکر شده باشند."
        )

    logger.info(
        "budget extraction finished: %d document(s), %d cell(s)",
        len(documents),
        sum(len(doc.cells) for doc in documents),
    )
    return ExtractionBundle(
        documents=documents,
        base_year=base_year,
        current_year=current_year,
        warnings=warnings,
    )
