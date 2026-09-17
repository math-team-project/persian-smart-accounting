"""فهرست فرم‌های شناخته‌شده‌ی بودجه و تطبیق معنایی آن‌ها با شیت‌های سند.

طبق بخش «ساختار فرم‌های شناخته‌شده» پرامپت مرجع، شماره‌ی ردیف و موقعیت دقیق
اقلام در فایل‌های مختلف تغییر می‌کند؛ به همین دلیل شناسایی فرم‌ها بر پایه‌ی
**محتوا و عنوان** انجام می‌شود (fuzzy، با همان روش ``rapidfuzz`` که در استخراج
بودجه‌ی موجود پروژه استفاده شده) و نه بر پایه‌ی موقعیت سلول یا نام دقیق شیت.

این فهرست هم‌زمان مرجع «فرم‌های مورد انتظار» است: فرم‌هایی که در سند پیدا نشوند
در ``DocumentExtraction.missing_form_keys`` ثبت می‌شوند تا در گزارش به‌صراحت
«در اسناد موجود نیست» اعلام شوند -- نه اینکه بی‌صدا حذف شوند.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from budget_analysis.text import clean_cell, match_ratio, normalize_key

__all__ = [
    "FORM_KEYWORDS",
    "FORM_LOOKUP",
    "FORMS",
    "FormDefinition",
    "FormMatch",
    "form_name",
    "match_forms",
    "score_form",
]

# امتیاز لازم برای پذیرش یک تطبیق فرم (۰ تا ۱۰۰).
DEFAULT_MIN_SCORE = 68.0
# امتیاز لازم برای «عبارت کلیدی قوی» (مثل «5-1» که خودش تقریباً یک شناسه است).
STRONG_KEYWORD_CUTOFF = 88.0
STRONG_BOOST = 15.0
# امتیاز لازم برای آنکه یک عبارت کلیدی «یافته‌شده» شمرده شود (برای مستندسازی).
TERM_CUTOFF = 72.0


@dataclass(frozen=True)
class FormDefinition:
    """توصیف یک فرم بودجه: نام، عبارت‌های کلیدی محتوایی و راهنمای درج در پرامپت."""

    key: str
    name_fa: str
    content_hint: str
    keywords: tuple[str, ...]
    strong_keywords: tuple[str, ...] = ()


# ترتیب این تاپل ترتیب اولویت در تساوی امتیازهاست. ترتیب مهم است: شناسه‌های
# مشتق (۵-۱ پیش از ۵، ۹ پیش از ۱) ابتدا می‌آیند تا در امتیاز مساوی برنده شوند.
FORMS: tuple[FormDefinition, ...] = (
    FormDefinition(
        key="form_5_1",
        name_fa="فرم ۵-۱ ـ مصارف و تفکیک هزینه‌ها و اقلام هزینه‌ای",
        content_hint=(
            "تفکیک ریز اقلام هزینه‌ای (حقوق، مزایا، رفاه، قراردادی، کارگری، شرکتی)؛ "
            "مبنای اصلی محورهای رشد حقوق و ساختار هزینه نیروی انسانی."
        ),
        keywords=(
            "مصارف و تفکیک هزینه",
            "تفکیک هزینه ها",
            "اقلام هزینه ای",
            "هزینه های پرسنلی",
            "حقوق و دستمزد",
            "مزایای غیرمستمر",
            "هزینه های رفاهی",
        ),
        strong_keywords=("5-1", "5 1", "5_1"),
    ),
    FormDefinition(
        key="form_5",
        name_fa="فرم ۵ ـ مصارف و تفکیک هزینه‌ها",
        content_hint="سرفصل‌های مصارف و تفکیک هزینه‌ها (سطح بالاتر از فرم ۵-۱).",
        keywords=(
            "مصارف و تفکیک هزینه",
            "تفکیک هزینه ها",
            "هزینه های جاری",
            "هزینه های سرمایه ای",
            "فصل هزینه",
        ),
    ),
    FormDefinition(
        key="form_9",
        name_fa="فرم ۹ ـ اعتبارات متفرقه تملک دارایی‌های سرمایه‌ای",
        content_hint="اعتبارات متفرقه در بخش تملک دارایی‌های سرمایه‌ای.",
        keywords=(
            "اعتبارات متفرقه تملک دارایی",
            "متفرقه تملک",
            "متفرقه دارایی های سرمایه ای",
            "اعتبارات متفرقه سرمایه ای",
        ),
    ),
    FormDefinition(
        key="form_1",
        name_fa="فرم ۱ ـ منابع و مصارف",
        content_hint=(
            "منابع و مصارف، جمع منابع، جمع مصارف، مانده‌ها، منابع عمومی، درآمد اختصاصی، "
            "اعتبارات متفرقه، سایر منابع و تملک دارایی‌ها؛ مبنای محور مانده و انتقال سنواتی."
        ),
        keywords=(
            "منابع و مصارف",
            "جمع منابع",
            "جمع مصارف",
            "مانده اعتبارات",
            "منابع عمومی",
            "درآمد اختصاصی",
            "درآمدهای اختصاصی",
            "اعتبارات متفرقه متمرکز",
            "سایر منابع",
            "تملک دارایی",
        ),
    ),
    FormDefinition(
        key="form_2",
        name_fa="فرم ۲ ـ اعتبارات به تفکیک برنامه",
        content_hint=(
            "اعتبارات به تفکیک برنامه؛ از جمله توسعه فناوری و فن‌آفرینی و "
            "تعمیرات اساسی و خرید تجهیزات."
        ),
        keywords=(
            "اعتبارات به تفکیک برنامه",
            "تفکیک برنامه",
            "توسعه فناوری و فن افرینی",
            "فن افرینی",
            "تعمیرات اساسی و خرید تجهیزات",
            "برنامه های",
        ),
    ),
    FormDefinition(
        key="form_3",
        name_fa="فرم ۳ ـ اهداف، چالش‌ها و دستاوردها",
        content_hint="ماهیت عمدتاً کیفی (اهداف، چالش‌ها، دستاوردها) -- مبنای مالی مستقیم نیست.",
        keywords=(
            "اهداف و چالش",
            "چالش ها و دستاورد",
            "دستاوردها",
            "اهداف کمی",
            "چالش ها",
        ),
    ),
    FormDefinition(
        key="form_4",
        name_fa="فرم ۴ ـ طرح‌ها و پروژه‌های تملک دارایی‌های سرمایه‌ای / عمرانی",
        content_hint=(
            "پروژه‌های عمرانی و تملک دارایی سرمایه‌ای؛ از جمله خرید تجهیزات، نیروگاه خورشیدی، "
            "کارگاه‌ها، آبرسانی، مراکز نوآوری و پروژه‌های ساختمانی."
        ),
        keywords=(
            "طرح ها و پروژه",
            "پروژه های تملک دارایی",
            "تملک دارایی های سرمایه ای",
            "پروژه های عمرانی",
            "طرح های عمرانی",
            "نیروگاه خورشیدی",
            "ابرسانی",
            "مراکز نو اوری",
            "پروژه های ساختمانی",
        ),
    ),
    FormDefinition(
        key="form_6",
        name_fa="فرم ۶ ـ آمار کارکنان غیر هیأت علمی و اعضای هیأت علمی",
        content_hint=(
            "آمار کارکنان و اعضای هیأت علمی به تفکیک استخدام، تحصیلات و رده علمی، "
            "همراه با توضیحات استخدام/خروج/بازنشستگی؛ مبنای محور ثبات نیروی انسانی."
        ),
        keywords=(
            "آمار کارکنان",
            "کارکنان غیر هیات علمی",
            "اعضای هیات علمی",
            "هیات علمی",
            "نیروی انسانی",
            "ترکیب استخدامی",
            "ترکیب تحصیلی",
        ),
    ),
    FormDefinition(
        key="form_7",
        name_fa="فرم ۷ ـ آمار دانشجویی و گروه‌های تحصیلی",
        content_hint="آمار دانشجویی، گروه‌های تحصیلی، دانش‌آموختگان.",
        keywords=(
            "آمار دانشجویی",
            "گروه های تحصیلی",
            "دانشجویان",
            "دانش اموختگان",
            "آمار دانشجو",
        ),
    ),
    FormDefinition(
        key="form_8",
        name_fa="فرم ۸ ـ اعتبارات متفرقه هزینه‌ای",
        content_hint=(
            "اعتبارات متفرقه هزینه‌ای؛ از جمله توسعه فناوری، افزایش سرمایه صندوق و "
            "بسته‌های حمایتی."
        ),
        keywords=(
            "اعتبارات متفرقه هزینه ای",
            "افزایش سرمایه صندوق",
            "بسته های حمایتی",
            "توسعه فناوری",
            "متفرقه هزینه ای",
        ),
    ),
    FormDefinition(
        key="form_10",
        name_fa="فرم ۱۰ ـ شاخص‌های عملکردی و فرایندی",
        content_hint=(
            "شاخص‌های عملکردی و فرایندی (از جمله شاخص‌های نیروی انسانی) که باید با "
            "فرم ۵-۱ تطبیق داده شوند."
        ),
        keywords=(
            "شاخص های عملکردی",
            "شاخص های فرایندی",
            "شاخص عملکرد",
            "شاخص های کلیدی",
            "شاخص های پایش",
        ),
    ),
)

FORM_LOOKUP: dict[str, FormDefinition] = {form.key: form for form in FORMS}

# همه‌ی عبارت‌های کلیدی در یک تاپل -- برای تشخیص «آینه‌ای‌بودن» متن PDF که باید
# بر پایه‌ی واژگان دامنه (بودجه) تصمیم بگیرد، نه واژه‌های عمومی.
FORM_KEYWORDS: tuple[str, ...] = tuple(
    dict.fromkeys(
        keyword
        for form in FORMS
        for keyword in (*form.strong_keywords, *form.keywords)
    )
)


@dataclass(frozen=True)
class FormMatch:
    """نتیجه‌ی تطبیق یک شیت با یک فرم."""

    form_key: str
    form_name: str
    sheet_index: int
    sheet_name: str
    score: float
    matched_terms: tuple[str, ...]


def form_name(form_key: str) -> str:
    form = FORM_LOOKUP.get(form_key)
    return form.name_fa if form else form_key


def score_form(form: FormDefinition, text: str) -> tuple[float, tuple[str, ...]]:
    """امتیاز یک فرم نسبت به متن یک شیت (۰ تا ۱۰۰) و عبارت‌های کلیدی یافت‌شده."""
    best = 0.0
    matched: list[str] = []
    for keyword in form.keywords:
        ratio = match_ratio(keyword, text)
        if ratio >= TERM_CUTOFF:
            matched.append(keyword)
        best = max(best, ratio)

    boost = 0.0
    for strong in form.strong_keywords:
        if match_ratio(strong, text) >= STRONG_KEYWORD_CUTOFF:
            boost = STRONG_BOOST
            matched.append(strong)

    return min(100.0, best + boost), tuple(dict.fromkeys(matched))


def match_forms(
    sheets: Sequence[tuple[str, str]],
    *,
    min_score: float = DEFAULT_MIN_SCORE,
    exclude_keys: Iterable[str] = (),
) -> tuple[list[FormMatch], list[int]]:
    """تطبیق یک‌به‌یک شیت‌ها با فرم‌ها به روش حریصانه (بالاترین امتیاز اول).

    ``sheets`` فهرستی از ``(نام شیت, متن نمونه)`` است. خروجی: تطبیق‌های پذیرفته‌شده
    و فهرست اندیس شیت‌هایی که به هیچ فرمی نسبت داده نشدند. یک فرم هرگز به دو شیت
    نسبت داده نمی‌شود (اگر سند دو شیت هم‌نام داشته باشد، تنها بهترین آن‌ها فرم را
    می‌گیرد و شیت دیگر مشکوک/بدون‌فرم ثبت می‌شود) تا محورها بر پایه‌ی داده‌ی
    تکراری محاسبه نشوند.
    """
    excluded = set(exclude_keys)
    candidates: list[tuple[float, int, str, tuple[str, ...]]] = []
    for sheet_index, (sheet_name, sample) in enumerate(sheets):
        haystack = f"{clean_cell(sheet_name)} {sample}"
        if not normalize_key(haystack):
            continue
        for form in FORMS:
            if form.key in excluded:
                continue
            score, terms = score_form(form, haystack)
            if score >= min_score:
                candidates.append((score, sheet_index, form.key, terms))

    # بالاترین امتیاز اول؛ در تساوی، اولویت با ترتیب تعریف فرم‌ها و سپس شیت اول.
    order = {form.key: index for index, form in enumerate(FORMS)}
    candidates.sort(key=lambda item: (-item[0], order[item[2]], item[1]))

    used_sheets: set[int] = set()
    used_forms: set[str] = set()
    matches: list[FormMatch] = []
    for score, sheet_index, form_key, terms in candidates:
        if sheet_index in used_sheets or form_key in used_forms:
            continue
        used_sheets.add(sheet_index)
        used_forms.add(form_key)
        matches.append(
            FormMatch(
                form_key=form_key,
                form_name=form_name(form_key),
                sheet_index=sheet_index,
                sheet_name=sheets[sheet_index][0],
                score=round(score, 1),
                matched_terms=terms,
            )
        )

    unmatched = [
        index
        for index, (sheet_name, _sample) in enumerate(sheets)
        if index not in used_sheets and normalize_key(sheet_name)
    ]
    matches.sort(key=lambda match: match.sheet_index)
    return matches, unmatched


def expected_form_keys() -> list[str]:
    """کلید همه‌ی فرم‌های شناخته‌شده (برای گزارش فرم‌های پیدا‌نشده)."""
    return [form.key for form in FORMS]
