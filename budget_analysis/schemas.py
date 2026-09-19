"""مدل‌های داده‌ی خط تحلیل بودجه (Pydantic).

دو خانواده مدل این‌جا هست:

۱) **نمایش میانی مرحله‌ی ۱** (``ExtractionBundle`` و اجزایش): ساختارمند‌شده‌ی
   اسناد ورودی -- فرم/بخش/ردیف/ستون/مقدار/واحد/سال/محل سلول. این ساختار داده‌ی
   ورودی مرحله‌ی ۲ و همچنین داده‌ی ردیابی (traceability) یافته‌های گزارش است.

۲) **خروجی مرحله‌ی ۲** (``BudgetAnalysisReport`` و اجزایش): همان JSON سختگیرانه‌ای
   که مدل زبانی باید برگرداند. اعتبارسنجی این مدل‌ها همان «دروازه‌ی کیفیت» است؛
   اگر خروجی مدل از این ساختار تبعیت نکند، اجرا با پیام خطای اعتبارسنجی یک‌بار
   تکرار و در صورت تکرار خطا، ناموفق اعلام می‌شود.

نکته‌ی مهم اعتبارسنجی (اصل ۳ پرامپت مرجع): هر ردیف ماتریس خطادهی *دارای انحراف*
باید محل داده را داشته باشد (فرم + حداقل یکی از ردیف/ستون/شواهد). ردیف‌های
«معاف/فاقد داده کافی/عادی» از این الزام مستثنا هستند، چون ذاتاً ممکن است قلم
مشخصی نداشته باشند؛ اما متن آن‌ها به‌صورت خودکار به شکل استاندارد
«در اسناد موجود نیست» درمی‌آید.
"""
from __future__ import annotations

import re
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from budget_analysis import MISSING_TEXT, NOT_COMPUTABLE_TEXT
from budget_analysis.text import clean_cell

__all__ = [
    "IMPORTANCE_VOCAB",
    "STATUS_VOCAB",
    "AxisDashboardRow",
    "BudgetAnalysisReport",
    "DocumentExtraction",
    "ErrorMatrixRow",
    "ExecutiveSummary",
    "ExtractedCell",
    "ExtractionBundle",
    "FormExtraction",
    "FindingEvidence",
    "ItemNeedingClarification",
    "ItemNeedingDecision",
    "ReportMeta",
    "RiskItem",
    "SignificantFinding",
    "TopFinding",
    "normalize_importance",
    "normalize_status",
]

# واژگان وضعیت و اهمیت -- دقیقاً همان دو فهرست بخش «وضعیت و اهمیت» پرامپت مرجع.
STATUS_VOCAB: tuple[str, ...] = (
    "عادی",
    "نزدیک به حد",
    "نیازمند بررسی",
    "هشدار مدیریتی",
    "مغایرت بااهمیت",
    "ریسک بااهمیت",
    "فاقد داده کافی",
    "معاف",
)

IMPORTANCE_VOCAB: tuple[str, ...] = (
    "عادی",
    "قابل توجه",
    "بااهمیت",
    "بسیار بالا",
)

Difficulty = Literal["مالی", "اجرایی", "ساختاری", "پایداری", "سایر"]

# نگاشت مترادف‌ها به واژگان مجاز. هدف این نیست که خروجی بدشکل «پذیرفته» شود؛
# فقط واریانت‌های نگارشی رایج (نیم‌فاصله/فاصله/هم‌معنی نزدیک) یکدست می‌شوند و
# هر مقدار واقعاً ناشناخته باعث خطای اعتبارسنجی (و در نتیجه تکرار) می‌شود.
_STATUS_ALIASES = {
    "عادی": "عادی",
    "نزدیک به حد": "نزدیک به حد",
    "نزدیک به حد مجاز": "نزدیک به حد",
    "نیازمند بررسی": "نیازمند بررسی",
    "نیازمند بررسی بیشتر": "نیازمند بررسی",
    # واریانت نگارشی متن قواعد خودِ سامانه (``criteria.py``): «مورد نیازمند بررسی»
    # عیناً در چند «قاعده ارزیابی» آمده و مدل آن را به‌عنوان وضعیت برمی‌گرداند.
    "مورد نیازمند بررسی": "نیازمند بررسی",
    "هشدار": "هشدار مدیریتی",
    "هشدار مدیریتی": "هشدار مدیریتی",
    "مغایرت": "مغایرت بااهمیت",
    "مغایرت بااهمیت": "مغایرت بااهمیت",
    "ریسک": "ریسک بااهمیت",
    "ریسک بااهمیت": "ریسک بااهمیت",
    "فاقد داده": "فاقد داده کافی",
    "فاقد داده کافی": "فاقد داده کافی",
    "داده کافی نیست": "فاقد داده کافی",
    # متن‌های استاندارد خودِ سامانه برای «داده‌ی موجود نیست» از نظر معنایی همان
    # «فاقد داده کافی» هستند (و ``_fill_missing_data_text`` هم متن نتیجه را از
    # همین وضعیت می‌سازد)، بنابراین اگر مدل آن‌ها را در فیلد وضعیت بگذارد،
    # ترجمه می‌شوند -- نه اینکه کل خروجی رد شود.
    NOT_COMPUTABLE_TEXT: "فاقد داده کافی",
    MISSING_TEXT: "فاقد داده کافی",
    "معاف": "معاف",
}

_IMPORTANCE_ALIASES = {
    "عادی": "عادی",
    "کم": "عادی",
    "پایین": "عادی",
    "قابل توجه": "قابل توجه",
    "متوسط": "قابل توجه",
    "بااهمیت": "بااهمیت",
    "با اهمیت": "بااهمیت",
    "بالا": "بااهمیت",
    "بسیار بالا": "بسیار بالا",
    "بسیار زیاد": "بسیار بالا",
    "حیاتی": "بسیار بالا",
    "بحرانی": "بسیار بالا",
}

_KEY_ALIASES = {
    "status": "وضعیت",
    "importance": "اهمیت",
}

# ردیف‌هایی که ذاتاً ممکن است قلم مشخصی نداشته باشند و الزام ردیابی بر آن‌ها
# اعمال نمی‌شود (در مقابل، هر ردیف دارای انحراف باید محل داده را بدهد).
_TRACEABILITY_EXEMPT_STATUSES = {"معاف", "فاقد داده کافی", "عادی"}


def _clean_key(value: object) -> str:
    return re.sub(r"\s+", " ", clean_cell(value)).strip()


def normalize_status(value: object) -> str:
    """مقدار وضعیت را به یکی از واژگان مجاز تبدیل می‌کند یا خطا می‌دهد."""
    key = re.sub(r"\s*با\s*اهمیت", "بااهمیت", _clean_key(value))
    if key in STATUS_VOCAB:
        return key
    if key in _STATUS_ALIASES:
        return _STATUS_ALIASES[key]
    condensed = key.replace(" ", "")
    for allowed in STATUS_VOCAB:
        if condensed == allowed.replace(" ", ""):
            return allowed
    raise ValueError(
        f"وضعیت «{key}» مجاز نیست. مقادیر مجاز: {', '.join(STATUS_VOCAB)}"
    )


def normalize_importance(value: object) -> str:
    """مقدار اهمیت را به یکی از واژگان مجاز تبدیل می‌کند یا خطا می‌دهد."""
    key = _clean_key(value)
    key = re.sub(r"\s*با\s*اهمیت", "بااهمیت", key)
    if key in IMPORTANCE_VOCAB:
        return key
    if key in _IMPORTANCE_ALIASES:
        return _IMPORTANCE_ALIASES[key]
    condensed = key.replace(" ", "")
    for allowed in IMPORTANCE_VOCAB:
        if condensed == allowed.replace(" ", ""):
            return allowed
    raise ValueError(
        f"اهمیت «{key}» مجاز نیست. مقادیر مجاز: {', '.join(IMPORTANCE_VOCAB)}"
    )


class _BaseModel(BaseModel):
    """پایه‌ی مشترک: فیلدهای اضافی مدل نادیده گرفته می‌شوند (باعث تکرار بی‌دلیل نشوند)."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True, populate_by_name=True)


# ---------------------------------------------------------------------------
# مرحله ۱ -- نمایش میانی ساختارمند
# ---------------------------------------------------------------------------
class ExtractedCell(_BaseModel):
    """یک داده‌ی عددی همراه با محل معنایی و فیزیکی آن در سند."""

    form_key: str
    form_name: str
    section: Optional[str] = None
    row_title: str
    row_number: Optional[int] = None
    column_title: str
    column_index: int = 0
    value: float
    raw_value: str = ""
    unit: Optional[str] = None
    year: Optional[str] = None
    cell_ref: Optional[str] = None
    confidence: float = 0.0
    source_document: str


class FormExtraction(_BaseModel):
    """فرم شناسایی‌شده در یک سند."""

    form_key: str
    form_name: str
    sheet_name: str = ""
    confidence: float = 0.0
    matched_terms: list[str] = Field(default_factory=list)
    section_titles: list[str] = Field(default_factory=list)
    row_titles: list[str] = Field(default_factory=list)
    column_titles: list[str] = Field(default_factory=list)
    cell_count: int = 0


class DocumentExtraction(_BaseModel):
    """نتیجه‌ی استخراج یک سند (سال پایه یا سال جاری)."""

    slot: Literal["base_year", "current_year"]
    role_fa: str
    filename: str
    detected_year: Optional[str] = None
    unit: Optional[str] = None
    extraction_method: str = ""
    mirrored_text_repaired: bool = False
    forms: list[FormExtraction] = Field(default_factory=list)
    missing_form_keys: list[str] = Field(default_factory=list)
    unsupported_sheets: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    cells: list[ExtractedCell] = Field(default_factory=list)
    anchors: dict[str, float] = Field(default_factory=dict)
    cell_limit_reached: bool = False


class ExtractionBundle(_BaseModel):
    """داده‌ی کامل مرحله‌ی ۱: هر دو سند + سال‌های نهایی‌شده + هشدارهای کلان."""

    documents: list[DocumentExtraction] = Field(default_factory=list)
    base_year: Optional[str] = None
    current_year: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)

    @property
    def total_cells(self) -> int:
        return sum(len(document.cells) for document in self.documents)

    def document(self, slot: str) -> Optional[DocumentExtraction]:
        for document in self.documents:
            if document.slot == slot:
                return document
        return None


# ---------------------------------------------------------------------------
# مرحله ۲ -- خروجی ساختاریافته‌ی تحلیل
# ---------------------------------------------------------------------------
class ReportMeta(_BaseModel):
    organization: str = MISSING_TEXT
    current_year: str = MISSING_TEXT
    base_year: str = MISSING_TEXT
    source_documents: list[str] = Field(default_factory=list)


class ExecutiveSummary(_BaseModel):
    """پاسخ به شش پرسش بخش «جمع‌بندی مدیریتی نهایی» پرامپت مرجع."""

    overall_status: str = ""
    top_deviation: str = ""
    top_financial_risk: str = ""
    top_structural_risk: str = ""
    top_mission_issue: str = ""
    top_recommended_decision: str = ""


class AxisDashboardRow(_BaseModel):
    axis: str
    status: str
    importance: str = "عادی"
    significant_findings_count: int = 0
    management_summary: str = ""

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: Any) -> str:
        return normalize_status(value)

    @field_validator("importance", mode="before")
    @classmethod
    def _importance(cls, value: Any) -> str:
        return normalize_importance(value)


class ErrorMatrixRow(_BaseModel):
    """یک ردیف ماتریس خطادهی سیستمی (بخش ۴ پرامپت مرجع)."""

    criterion_id: str
    axis: str
    criterion: str = ""
    result: str = ""
    status: str
    importance: str = "عادی"
    form: str = ""
    section: str = ""
    row: str = ""
    column: str = ""
    base_value: Optional[Union[float, str]] = None
    current_value: Optional[Union[float, str]] = None
    threshold: Optional[Union[float, str]] = None
    absolute_deviation: Optional[Union[float, str]] = None
    percentage_deviation: Optional[Union[float, str]] = None
    comparison_type: str = ""
    base_document: str = ""
    comparison_document: str = ""
    base_year: str = ""
    comparison_year: str = ""
    evidence: str = ""
    action: str = ""

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: Any) -> str:
        return normalize_status(value)

    @field_validator("importance", mode="before")
    @classmethod
    def _importance(cls, value: Any) -> str:
        return normalize_importance(value)

    @field_validator(
        "base_value", "current_value", "threshold", "absolute_deviation", "percentage_deviation",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str) and not clean_cell(value):
            return None
        return value

    @property
    def is_deviation(self) -> bool:
        return self.status not in _TRACEABILITY_EXEMPT_STATUSES

    @model_validator(mode="after")
    def _ensure_traceability(self) -> "ErrorMatrixRow":
        """اصل ۳: هر ردیف دارای انحراف باید فرم و محل داده را داشته باشد.

        این بررسی همان دلیلی است که خروجی ناقص مدل باعث تکرار (retry) می‌شود و
        هرگز بی‌سروصدا پذیرفته نمی‌شود.
        """
        if not self.is_deviation:
            return self
        if not clean_cell(self.form) or clean_cell(self.form) == MISSING_TEXT:
            raise ValueError(
                f"ردیف {self.criterion_id}: ردیف دارای انحراف بدون «فرم» قابل قبول نیست "
                "(اصل ردیابی کامل نتایج)."
            )
        if not any(clean_cell(part) for part in (self.row, self.column, self.evidence)):
            raise ValueError(
                f"ردیف {self.criterion_id}: ردیف دارای انحراف باید حداقل یکی از "
                "«ردیف/ستون/شواهد» را داشته باشد."
            )
        return self

    @model_validator(mode="after")
    def _fill_missing_data_text(self) -> "ErrorMatrixRow":
        """ردیف «فاقد داده کافی» به شکل استاندارد پرامپت مرجع نوشته می‌شود."""
        if self.status == "فاقد داده کافی":
            if not clean_cell(self.result) or clean_cell(self.result) == "-":
                self.result = NOT_COMPUTABLE_TEXT
            if not clean_cell(self.form):
                self.form = MISSING_TEXT
        return self


class TopFinding(_BaseModel):
    """یک ردیف جدول «مهم‌ترین انحرافات عددی» (بخش ۱۳ پرامپت مرجع)."""

    rank: Optional[int] = None
    axis: str = ""
    criterion: str = ""
    location: str = ""
    observed_value: Optional[Union[float, str]] = None
    reference_value: Optional[Union[float, str]] = None
    deviation: Optional[Union[float, str]] = None
    deviation_unit: str = ""
    importance: str = "عادی"
    management_message: str = ""

    @field_validator("importance", mode="before")
    @classmethod
    def _importance(cls, value: Any) -> str:
        return normalize_importance(value)


class FindingEvidence(_BaseModel):
    """«شواهد عددی» یک یافته‌ی بااهمیت (بخش ۱۴ پرامپت مرجع)."""

    form: str = MISSING_TEXT
    section: str = MISSING_TEXT
    row: str = MISSING_TEXT
    column: str = MISSING_TEXT
    base_value: Optional[Union[float, str]] = None
    current_value: Optional[Union[float, str]] = None
    threshold: Optional[Union[float, str]] = None
    absolute_deviation: Optional[Union[float, str]] = None
    percentage_deviation: Optional[Union[float, str]] = None


class SignificantFinding(_BaseModel):
    number: Optional[Union[int, str]] = None
    title: str
    subject: str = ""
    evidence: FindingEvidence = Field(default_factory=FindingEvidence)
    assessment: str = ""
    management_importance: str = ""
    risk: str = ""
    action: str = ""


class RiskItem(_BaseModel):
    """ردیف جدول «ریسک‌ها و آثار مالی» (بخش ۱۶ پرامپت مرجع)."""

    description: str
    approximate_amount: Union[float, str] = "قابل تعیین از اسناد موجود نیست"
    importance: str = "عادی"
    nature: Difficulty = "مالی"

    @field_validator("importance", mode="before")
    @classmethod
    def _importance(cls, value: Any) -> str:
        return normalize_importance(value)

    @field_validator("nature", mode="before")
    @classmethod
    def _nature(cls, value: Any) -> str:
        text = _clean_key(value)
        if not text:
            return "سایر"
        for allowed in ("مالی", "اجرایی", "ساختاری", "پایداری"):
            if allowed in text:
                return allowed
        return "سایر"


class ItemNeedingDecision(_BaseModel):
    """موضوع نیازمند تصمیم‌گیری (بخش ۱۷ پرامپت مرجع)."""

    subject: str
    question: str = ""
    proposed_action: str = ""


class ItemNeedingClarification(_BaseModel):
    """مورد نیازمند شفاف‌سازی (بخش ۱۸ پرامپت مرجع)."""

    subject: str
    available_data: str = MISSING_TEXT
    missing_or_conflicting_data: str = ""
    location: str = MISSING_TEXT
    reason: str = ""
    required_document: str = MISSING_TEXT


class BudgetAnalysisReport(_BaseModel):
    """کل خروجی ساختاریافته‌ی مرحله‌ی ۲ -- همان چیزی که مرحله‌ی ۳ رندر می‌کند."""

    meta: ReportMeta = Field(default_factory=ReportMeta)
    executive_summary: ExecutiveSummary = Field(default_factory=ExecutiveSummary)
    axis_dashboard: list[AxisDashboardRow] = Field(default_factory=list)
    error_matrix: list[ErrorMatrixRow] = Field(default_factory=list)
    top_findings: list[TopFinding] = Field(default_factory=list)
    significant_findings: list[SignificantFinding] = Field(default_factory=list)
    risks: list[RiskItem] = Field(default_factory=list)
    items_needing_decision: list[ItemNeedingDecision] = Field(default_factory=list)
    items_needing_clarification: list[ItemNeedingClarification] = Field(default_factory=list)
    closing_notes: list[str] = Field(default_factory=list)

    # ------------------------------------------------------------------
    @property
    def deviation_rows(self) -> list[ErrorMatrixRow]:
        return [row for row in self.error_matrix if row.is_deviation]

    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.error_matrix:
            counts[row.status] = counts.get(row.status, 0) + 1
        return counts

    def summary_chips(self) -> list[str]:
        """برچسب‌های شمارشی کوتاه برای ردیف تاریخچه‌ی پروژه."""
        counts = self.status_counts()
        chips = [
            f"{len(self.deviation_rows)} مورد دارای انحراف از {len(self.error_matrix)} معیار",
            f"{len(self.significant_findings)} یافته بااهمیت",
            f"{len(self.risks)} ریسک",
            f"{len(self.items_needing_decision)} مورد نیازمند تصمیم",
        ]
        if counts.get("فاقد داده کافی"):
            chips.append(f"{counts['فاقد داده کافی']} معیار فاقد داده کافی")
        return chips
