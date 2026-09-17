"""مرحله‌ی ۳ خط پردازش: رندر قطعی (deterministic) گزارش Word.

ورودی این مرحله فقط ``BudgetAnalysisReport`` معتبرشده است؛ هیچ فراخوانی مدل
زبانی این‌جا وجود ندارد. ترتیب بخش‌ها دقیقاً همان «قالب نهایی گزارش» پرامپت مرجع
است و قواعد قالب‌بندی (RTL، راست‌چین، سربرگ تکرارشونده‌ی جدول، واحد در سربرگ
ستون‌ها، ارقام فارسی، رنگ‌بندی همراه با متن وضعیت) همه در کد اعمال می‌شوند.

قاعده‌ی مهم نمایشی: هیچ مقدار خالی «-» نمی‌شود؛ اگر داده‌ای نباشد عبارت
«در اسناد موجود نیست» درج می‌شود تا فقدان داده در گزارش دیده شود.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from budget_analysis import MISSING_TEXT, NOT_COMPUTABLE_TEXT
from budget_analysis import docx_rtl as rtl
from budget_analysis.schemas import (
    AxisDashboardRow,
    BudgetAnalysisReport,
    ErrorMatrixRow,
    FindingEvidence,
)
from budget_analysis.text import clean_cell, fa_digits, fa_number

logger = logging.getLogger(__name__)

__all__ = ["ReportContext", "render_report_bytes", "render_report_docx"]

REPORT_TITLE = "گزارش مدیریتی یکپارچه پایش و ارزیابی بودجه"

NATURE_OF_REPORT_TEXT = (
    "این سند یک خلاصه و تحلیل مدیریتی مبتنی بر اسناد بودجه است که برای تسهیل بررسی و "
    "تصمیم‌گیری در جلسه مدیریت / هیأت امنا تهیه شده است. اسناد اصلی بودجه و اصلاحیه، "
    "مرجع رسمی و نهایی محسوب می‌شوند."
)

FOOTER_NOTE = (
    "این گزارش صرفاً بر پایه‌ی اسناد بودجه‌ی بارگذاری‌شده تهیه شده و جایگزین اسناد رسمی "
    "بودجه و اصلاحیه نیست؛ برای هر مورد انحراف، محل دقیق داده در همان اسناد ذکر شده است."
)


@dataclass
class ReportContext:
    """اطلاعات سربرگ گزارش -- بخشی از آن‌ها قطعی و در اختیار کد است، نه مدل."""

    organization: str = ""
    base_year: str = ""
    current_year: str = ""
    unit: Optional[str] = None
    source_documents: list[str] = field(default_factory=list)
    # تاریخ تهیه به‌صورت رشته‌ی آماده (لایه‌ی فراخوان تاریخ شمسی را می‌سازد تا این
    # پکیج به لایه‌ی وب وابسته نشود).
    report_date: str = ""
    font: str = rtl.DEFAULT_FONT

    def resolved(self, report: Optional[BudgetAnalysisReport] = None) -> "ReportContext":
        """مقادیر نهایی سربرگ: اولویت با مقدار قطعی، سپس مقدار خروجی مدل."""
        meta = report.meta if report else None
        return ReportContext(
            organization=self.organization or (meta.organization if meta else "") or MISSING_TEXT,
            base_year=self.base_year or (meta.base_year if meta else "") or MISSING_TEXT,
            current_year=self.current_year or (meta.current_year if meta else "") or MISSING_TEXT,
            unit=self.unit,
            source_documents=self.source_documents or (list(meta.source_documents) if meta else []),
            report_date=self.report_date or MISSING_TEXT,
            font=self.font,
        )


# ---------------------------------------------------------------------------
# کمک‌تابع‌های نمایشی
# ---------------------------------------------------------------------------
def _text(value: Any) -> str:
    """متن روایت‌گونه: ارقام به فارسی، خالی‌ها به «در اسناد موجود نیست»."""
    if value is None:
        return MISSING_TEXT
    if isinstance(value, bool):
        return fa_digits("بله" if value else "خیر")
    if isinstance(value, (int, float)):
        return fa_number(value)
    cleaned = clean_cell(value)
    if not cleaned:
        return MISSING_TEXT
    return fa_digits(cleaned)


def _value_text(value: Any) -> str:
    """مقدار عددی/متنی یک سلول گزارشی."""
    if value is None:
        return MISSING_TEXT
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if number.is_integer():
            return fa_number(number)
        return fa_number(number, decimals=2)
    return _text(value)


def _unit_suffix(unit: Optional[str], *, for_header: bool) -> str:
    if not unit:
        return ""
    return f" ({unit})" if for_header else f" {unit}"


def _location(row: ErrorMatrixRow, *, unit: Optional[str] = None) -> str:
    """«محل مشاهده» به‌صورت معنایی: فرم → بخش → ردیف → ستون → سال."""
    parts: list[str] = []
    for value in (row.form, row.section, row.row, row.column):
        cleaned = clean_cell(value)
        if cleaned and cleaned != MISSING_TEXT:
            parts.append(cleaned)
    year = clean_cell(row.comparison_year) or clean_cell(row.base_year)
    if year and year != MISSING_TEXT:
        parts.append(f"سال {year}")
    if not parts:
        return MISSING_TEXT
    return fa_digits(" ← ".join(parts))


def _axis_rows(report: BudgetAnalysisReport) -> list[AxisDashboardRow]:
    return report.axis_dashboard


# ---------------------------------------------------------------------------
# بخش‌های گزارش
# ---------------------------------------------------------------------------
def _render_front_matter(document, report: BudgetAnalysisReport, context: ReportContext) -> None:
    rtl.add_paragraph(
        document, REPORT_TITLE, size=17, bold=True, align_center=True, space_after=4
    )
    rtl.add_paragraph(
        document, context.organization, size=13, bold=True, align_center=True, space_after=10
    )
    meta_lines = [
        f"سال مورد بررسی: {fa_digits(context.current_year)}",
        f"سال پایه: {fa_digits(context.base_year)}",
        f"تاریخ تهیه گزارش: {fa_digits(context.report_date)}",
    ]
    if context.unit:
        meta_lines.append(f"واحد مبالغ: {context.unit}")
    rtl.add_paragraph(
        document, " | ".join(meta_lines), size=10.5, color=rtl.MUTED_COLOR, align_center=True
    )

    rtl.add_heading(document, "توضیح درباره ماهیت گزارش", level=2)
    rtl.add_paragraph(document, NATURE_OF_REPORT_TEXT, size=10.5, color=rtl.MUTED_COLOR)

    rtl.add_heading(document, "اسناد مبنای تحلیل", level=2)
    if context.source_documents:
        for name in context.source_documents:
            rtl.add_bullet(document, fa_digits(name), size=10.5)
    else:
        rtl.add_paragraph(document, MISSING_TEXT, size=10.5)


def _render_executive_summary(document, report: BudgetAnalysisReport) -> None:
    summary = report.executive_summary
    rtl.add_heading(document, "خلاصه مدیریتی", level=1)
    entries = (
        ("وضعیت کلی", summary.overall_status),
        ("مهم‌ترین انحراف", summary.top_deviation),
        ("مهم‌ترین ریسک مالی", summary.top_financial_risk),
        ("مهم‌ترین ریسک ساختاری", summary.top_structural_risk),
        ("مهم‌ترین موضوع مرتبط با مأموریت پارک", summary.top_mission_issue),
        ("مهم‌ترین تصمیم پیشنهادی", summary.top_recommended_decision),
    )
    if not any(clean_cell(value) for _label, value in entries):
        rtl.add_paragraph(document, "خلاصه‌ی مدیریتی ثبت نشده است.", size=11)
        return
    for label, value in entries:
        rtl.add_label_paragraph(document, label, _text(value), size=11)


def _render_status_summary(document, report: BudgetAnalysisReport) -> None:
    rtl.add_heading(document, "جمع‌بندی وضعیت", level=1)
    summary = report.executive_summary
    first_clarification = report.items_needing_clarification[0].subject if report.items_needing_clarification else ""
    first_risk = report.risks[0].description if report.risks else ""
    rtl.add_label_paragraph(document, "ارزیابی کلی", _text(summary.overall_status))
    rtl.add_label_paragraph(document, "مهم‌ترین موضوع قابل تصمیم‌گیری", _text(summary.top_recommended_decision))
    rtl.add_label_paragraph(
        document,
        "مهم‌ترین موضوع نیازمند شفاف‌سازی",
        _text(first_clarification) if first_clarification else MISSING_TEXT,
    )
    rtl.add_label_paragraph(
        document, "مهم‌ترین ریسک", _text(first_risk) if first_risk else _text(summary.top_financial_risk)
    )
    counts = report.status_counts()
    if counts:
        distribution = "، ".join(
            f"{status}: {fa_number(count)}" for status, count in sorted(counts.items())
        )
        rtl.add_label_paragraph(document, "توزیع وضعیت معیارها", distribution, size=10)


def _render_axis_dashboard(document, report: BudgetAnalysisReport) -> None:
    rtl.add_heading(document, "وضعیت محورهای پایش", level=1)
    if not report.axis_dashboard:
        rtl.add_paragraph(
            document,
            "وضعیت محورهای پایش در خروجی تحلیل ثبت نشده است.",
            size=10.5,
        )
        return
    rows = [
        [
            _text(row.axis),
            _text(row.status),
            _text(row.importance),
            _text(row.management_summary) if clean_cell(row.management_summary) else MISSING_TEXT,
            fa_number(row.significant_findings_count),
        ]
        for row in report.axis_dashboard
    ]
    rtl.add_table(
        document,
        ["محور پایش", "وضعیت", "اهمیت", "جمع‌بندی مدیریتی", "تعداد یافته"],
        rows,
        widths_cm=[2.6, 1.9, 1.4, 8.2, 1.4],
        row_shading=lambda _index, values: rtl.status_shading(values[1]),
    )


def _render_detail_table(document, report: BudgetAnalysisReport, context: ReportContext) -> None:
    rtl.add_heading(document, "جزئیات نتایج محورها و موارد انحراف", level=1)
    rtl.add_paragraph(
        document,
        "در این جدول همه‌ی معیارهای اجراشده ثبت شده‌اند (نه فقط موارد دارای انحراف)؛ "
        "معیارهایی که داده‌ی کافی نداشته‌اند با عبارت «"
        + NOT_COMPUTABLE_TEXT
        + "» مشخص شده‌اند.",
        size=10,
        color=rtl.MUTED_COLOR,
    )
    if not report.error_matrix:
        rtl.add_paragraph(document, "نتیجه‌ی معیاری ثبت نشده است.", size=10.5)
        return

    rows: list[list[str]] = []
    shadings: list[Optional[str]] = []
    for index, row in enumerate(report.error_matrix, start=1):
        rows.append(
            [
                fa_number(index),
                _text(row.axis),
                _text(row.criterion) if clean_cell(row.criterion) else MISSING_TEXT,
                _location(row),
                _value_text(row.base_value),
                _value_text(row.current_value),
                _value_text(row.threshold),
                _value_text(row.absolute_deviation),
                _value_text(row.percentage_deviation),
                _text(row.status),
                _text(row.importance),
            ]
        )
        shadings.append(rtl.status_shading(row.status))

    def shading(_index: int, values: Sequence[str]) -> Optional[str]:
        return shadings[_index] if _index < len(shadings) else None

    rtl.add_table(
        document,
        [
            "ردیف",
            "محور",
            "معیار",
            "محل مشاهده",
            f"مقدار مبنا{_unit_suffix(context.unit, for_header=True)}",
            f"مقدار جاری{_unit_suffix(context.unit, for_header=True)}",
            "حد مجاز",
            "انحراف مطلق",
            "انحراف نسبت به حد",
            "وضعیت",
            "اهمیت",
        ],
        rows,
        widths_cm=[0.8, 1.5, 1.8, 3.0, 1.3, 1.3, 1.25, 1.2, 1.25, 1.3, 1.2],
        cell_size=8,
        header_size=8.5,
        cant_split=False,
        row_shading=shading,
    )
    _render_insufficient_data_notes(document, report)


def _render_insufficient_data_notes(document, report: BudgetAnalysisReport) -> None:
    """معیارهایی که محاسبه‌شان ممکن نبوده را با عبارت استاندارد پرامپت مرجع فهرست می‌کند.

    این فهرست تضمین می‌کند فقدان داده در گزارش دیده شود (و با صفر یا خط تیره
    جایگزین نشود) -- طبق اصل «هرگز مقدار مفقود را با صفر جایگزین نکن».
    """
    rows = [row for row in report.error_matrix if row.status == "فاقد داده کافی"]
    if not rows:
        return
    rtl.add_paragraph(
        document,
        "محاسبه‌ی معیارهای زیر بر پایه‌ی اسناد موجود ممکن نبود و برای آن‌ها "
        f"«{NOT_COMPUTABLE_TEXT}» ثبت شده است:",
        size=10,
        bold=True,
        space_before=4,
    )
    for row in rows:
        place = _location(row)
        detail = clean_cell(row.evidence) or clean_cell(row.result) or NOT_COMPUTABLE_TEXT
        rtl.add_bullet(
            document,
            f"{_text(row.criterion) if clean_cell(row.criterion) else _text(row.axis)} — "
            f"{_text(detail)} (محل: {place})",
            size=9.5,
        )


def _render_top_findings(document, report: BudgetAnalysisReport, context: ReportContext) -> None:
    rtl.add_heading(document, "مهم‌ترین انحرافات عددی", level=1)
    if not report.top_findings:
        rtl.add_paragraph(document, "انحراف عددی قابل‌توجهی ثبت نشده است.", size=10.5)
        return
    rows = [
        [
            fa_number(finding.rank) if finding.rank else "—",
            _text(finding.axis),
            _text(finding.criterion),
            _text(finding.location),
            _value_text(finding.observed_value),
            _value_text(finding.reference_value),
            _value_text(finding.deviation),
            _text(finding.deviation_unit) if clean_cell(finding.deviation_unit) else "—",
            _text(finding.importance),
            _text(finding.management_message),
        ]
        for finding in report.top_findings
    ]
    rtl.add_table(
        document,
        [
            "رتبه",
            "محور",
            "معیار",
            "محل",
            f"مقدار مشاهده‌شده{_unit_suffix(context.unit, for_header=True)}",
            "مقدار مرجع",
            "انحراف",
            "درصد / واحد درصد انحراف",
            "اهمیت",
            "پیام مدیریتی",
        ],
        rows,
        widths_cm=[0.8, 1.5, 1.8, 2.6, 1.6, 1.5, 1.3, 1.7, 1.2, 2.4],
        cell_size=8,
        header_size=8.5,
        row_shading=lambda _index, values: rtl.importance_shading(values[8]),
    )


def _render_evidence(document, evidence: FindingEvidence, context: ReportContext) -> None:
    unit = _unit_suffix(context.unit, for_header=True)
    entries = (
        ("فرم", _text(evidence.form)),
        ("بخش", _text(evidence.section)),
        ("ردیف", _text(evidence.row)),
        ("ستون", _text(evidence.column)),
        (f"مقدار مبنا{unit}", _value_text(evidence.base_value)),
        (f"مقدار جاری / واقعی{unit}", _value_text(evidence.current_value)),
        ("حد مجاز / هدف", _value_text(evidence.threshold)),
        ("انحراف مطلق", _value_text(evidence.absolute_deviation)),
        ("انحراف درصدی / واحد درصدی", _value_text(evidence.percentage_deviation)),
    )
    for label, value in entries:
        rtl.add_label_paragraph(document, label, value, size=10, indent_cm=0.4)


def _render_significant_findings(document, report: BudgetAnalysisReport, context: ReportContext) -> None:
    rtl.add_heading(document, "یافته‌های بااهمیت", level=1)
    if not report.significant_findings:
        rtl.add_paragraph(
            document, "یافته‌ی بااهمیتی خارج از موارد جدول انحرافات ثبت نشده است.", size=10.5
        )
        return

    for index, finding in enumerate(report.significant_findings, start=1):
        number = finding.number if finding.number is not None else index
        rtl.add_paragraph(
            document,
            f"یافته {fa_digits(number)} ـ {_text(finding.title)}",
            size=12,
            bold=True,
            color=rtl.PRIMARY_COLOR,
            space_before=8,
            space_after=4,
            keep_with_next=True,
        )
        rtl.add_label_paragraph(document, "موضوع", _text(finding.subject), size=10.5)
        rtl.add_paragraph(document, "شواهد عددی", size=10.5, bold=True, space_before=4, space_after=3, keep_with_next=True)
        _render_evidence(document, finding.evidence, context)
        rtl.add_label_paragraph(document, "نتیجه ارزیابی", _text(finding.assessment), size=10.5)
        rtl.add_label_paragraph(
            document, "اهمیت مدیریتی", _text(finding.management_importance), size=10.5
        )
        rtl.add_label_paragraph(document, "ریسک و آثار احتمالی", _text(finding.risk), size=10.5)
        rtl.add_label_paragraph(document, "اقدام پیشنهادی", _text(finding.action), size=10.5)


def _render_risks(document, report: BudgetAnalysisReport, context: ReportContext) -> None:
    rtl.add_heading(document, "ریسک‌ها و آثار مالی", level=1)
    if not report.risks:
        rtl.add_paragraph(document, "ریسکی ثبت نشده است.", size=10.5)
        return
    rows = [
        [
            _text(risk.description),
            _value_text(risk.approximate_amount) if not isinstance(risk.approximate_amount, str) else _text(risk.approximate_amount),
            _text(risk.importance),
            _text(risk.nature),
        ]
        for risk in report.risks
    ]
    rtl.add_table(
        document,
        [
            "شرح ریسک",
            f"مبلغ تقریبی{_unit_suffix(context.unit, for_header=True)}",
            "درجه اهمیت",
            "ماهیت ریسک",
        ],
        rows,
        widths_cm=[8.6, 3.0, 2.3, 2.6],
        row_shading=lambda _index, values: rtl.importance_shading(values[2]),
    )


def _render_decisions(document, report: BudgetAnalysisReport) -> None:
    rtl.add_heading(document, "موارد نیازمند بررسی و تصمیم‌گیری", level=1)
    if not report.items_needing_decision:
        rtl.add_paragraph(document, "موردی برای تصمیم‌گیری ثبت نشده است.", size=10.5)
        return
    for index, item in enumerate(report.items_needing_decision, start=1):
        rtl.add_paragraph(
            document,
            f"{fa_number(index)}. {_text(item.subject)}",
            size=11,
            bold=True,
            space_before=6,
            space_after=3,
            keep_with_next=True,
        )
        rtl.add_label_paragraph(document, "پرسش پیشنهادی از مدیریت", _text(item.question), size=10.5, indent_cm=0.4)
        rtl.add_label_paragraph(document, "پیشنهاد اقدام / تصمیم", _text(item.proposed_action), size=10.5, indent_cm=0.4)


def _render_clarifications(document, report: BudgetAnalysisReport) -> None:
    rtl.add_heading(document, "موارد نیازمند شفاف‌سازی", level=1)
    if not report.items_needing_clarification:
        rtl.add_paragraph(document, "مورد نیازمند شفاف‌سازی ثبت نشده است.", size=10.5)
        return
    rows = [
        [
            _text(item.subject),
            _text(item.available_data),
            _text(item.missing_or_conflicting_data),
            _text(item.location),
            _text(item.reason),
            _text(item.required_document),
        ]
        for item in report.items_needing_clarification
    ]
    rtl.add_table(
        document,
        ["موضوع", "داده موجود", "داده مفقود یا متعارض", "محل مشاهده", "علت نیاز به شفاف‌سازی", "مستند موردنیاز"],
        rows,
        widths_cm=[2.3, 2.5, 2.8, 2.2, 3.0, 2.2],
    )


def _render_closing_notes(document, report: BudgetAnalysisReport) -> None:
    rtl.add_heading(document, "نکات تکمیلی برای جلسه", level=1)
    if not report.closing_notes:
        rtl.add_paragraph(document, "نکته‌ی تکمیلی ثبت نشده است.", size=10.5)
        return
    for note in report.closing_notes:
        rtl.add_bullet(document, _text(note), size=10.5)


# ---------------------------------------------------------------------------
# نقطه‌ی ورود
# ---------------------------------------------------------------------------
def _build_document(report: BudgetAnalysisReport, context: ReportContext):
    document = rtl.new_document(font=context.font)
    _render_front_matter(document, report, context)
    rtl.add_page_break(document)
    _render_executive_summary(document, report)
    _render_status_summary(document, report)
    _render_axis_dashboard(document, report)
    _render_detail_table(document, report, context)
    _render_top_findings(document, report, context)
    _render_significant_findings(document, report, context)
    _render_risks(document, report, context)
    _render_decisions(document, report)
    _render_clarifications(document, report)
    _render_closing_notes(document, report)
    rtl.add_paragraph(document, FOOTER_NOTE, size=9, color="777777", space_before=8)
    return document


def render_report_docx(
    report: BudgetAnalysisReport,
    context: ReportContext,
    output_path: str | Path,
) -> Path:
    """گزارش را می‌سازد و روی دیسک ذخیره می‌کند."""
    resolved = context.resolved(report)
    document = _build_document(report, resolved)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(path))
    logger.info("budget report written to %s", path.name)
    return path


def render_report_bytes(
    report: BudgetAnalysisReport,
    context: Optional[ReportContext] = None,
) -> bytes:
    """گزارش را در حافظه می‌سازد و بایت‌های ``.docx`` را برمی‌گرداند."""
    resolved = (context or ReportContext()).resolved(report)
    document = _build_document(report, resolved)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
