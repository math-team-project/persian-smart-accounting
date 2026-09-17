"""نرمال‌سازی متن/عدد و کمک‌تابع‌های متنی مشترک خط تحلیل بودجه.

نرمال‌سازی متن فارسی از همان ماژول موجود پروژه بازاستفاده می‌شود
(``extraction_script.scripts.checklist.normalize_persian.normalize_persian_text``)
تا «شناسایی معنایی» در سراسر پروژه یک رفتار واحد داشته باشد: یکسان‌سازی ی/ک،
تبدیل ارقام فارسی/عربی به لاتین، حذف نیم‌فاصله و فاصله‌های تکراری.

روی آن، این ماژول چیزهایی را می‌سازد که مخصوص این کارگاه است:
  * پارس عدد از سلول‌هایی که ممکن است رشته‌ی متنی، منفی داخل پرانتز یا خط تیره
    («-» یعنی «مقداری ثبت نشده»، نه صفر) باشند،
  * تشخیص سال و واحد از متن سند،
  * تشخیص «آینه‌ای‌بودن» متن استخراج‌شده از PDF و بازگرداندن آن (نگاه کنید به
    ``decide_mirroring`` -- خروجی PDF چاپ‌شده از اکسل، حروف فارسی را معکوس
    می‌دهد در حالی که ارقام سالم می‌مانند)،
  * قالب‌بندی نمایشی ارقام فارسی برای گزارش Word.
"""
from __future__ import annotations

import re
import sys
from numbers import Real
from pathlib import Path
from typing import Iterable, Optional, Sequence

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:  # اجرای مستقل پکیج بدون ریشه‌ی پروژه در sys.path
    sys.path.insert(0, str(ROOT_DIR))

from rapidfuzz import fuzz  # noqa: E402

# بازاستفاده‌ی بدون تغییر از نرمال‌ساز موجود پروژه (بدون کپی‌کردن منطق آن).
from extraction_script.scripts.checklist.normalize_persian import (  # noqa: E402
    normalize_persian_text,
)

__all__ = [
    "PERIOD_MARKERS",
    "clean_cell",
    "decide_mirroring",
    "detect_unit",
    "detect_years",
    "fa_digits",
    "fa_number",
    "fa_percent",
    "has_persian",
    "is_blank",
    "is_mostly_numeric",
    "keyword_hits",
    "match_ratio",
    "normalize_key",
    "normalize_persian_text",
    "parse_number",
    "unmirror",
]

_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
# جداکننده‌های فارسی: هزارگان «٬» و اعشار «٫» (تا ارقام فارسی ظاهر درستی هم داشته باشند).
_PERSIAN_NUMBER_MARKS = str.maketrans({",": "٬", ".": "٫", "%": "٪"})

# حروف فارسی/عربی (شامل فرم‌های presentation که در PDF ظاهر می‌شوند).
_PERSIAN_LETTER_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
# آرتیفکت شناخته‌شده‌ی استخراج فونت‌های جاسازی‌شده در PDF.
_CID_RE = re.compile(r"\(cid:\d+\)")
_YEAR_RE = re.compile(r"(?<!\d)(1[34]\d{2})(?!\d)")
_GREGORIAN_YEAR_RE = re.compile(r"(?<!\d)(20[2-4]\d)(?!\d)")
_NUMERIC_ALLOWED_RE = re.compile(r"[^\d\s.,٬٫\-+()%٪]")

# واحدهای متعارف گزارش‌های بودجه -- بلندترین اول تا «میلیون ریال» به «ریال» فروکاسته نشود.
UNIT_CANDIDATES: tuple[str, ...] = (
    "میلیارد ریال",
    "میلیون ریال",
    "هزار ریال",
    "میلیارد تومان",
    "میلیون تومان",
    "ریال",
)

# نشانگرهای سال در عنوان ستون‌ها و ردیف‌ها (برای تشخیص معنایی «سالِ هر ستون»).
PERIOD_MARKERS = (
    "عملکرد",
    "بودجه",
    "اصلاحیه",
    "مصوب",
    "ابلاغ",
    "تخصیص",
    "پیش بینی",
    "پیشبینی",
    "مانده",
    "انتقالی",
)


def fa_digits(value: object) -> str:
    """ارقام لاتین را به ارقام فارسی تبدیل می‌کند (بقیه‌ی نویسه‌ها دست‌نخورده)."""
    return str("" if value is None else value).translate(_PERSIAN_DIGITS)


def fa_number(value: object, decimals: int = 0) -> str:
    """عدد را با جداکننده‌ی هزارگان و ارقام فارسی برمی‌گرداند.

    مقدار ``None`` یا رشته‌ی خالی به خط تیره تبدیل می‌شود -- نه به صفر (اصل
    «هرگز مقدار مفقود را با صفر جایگزین نکن»).
    """
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return fa_digits(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fa_digits(value)
    text = f"{number:,.{decimals}f}" if decimals > 0 else f"{number:,.0f}"
    return text.translate(_PERSIAN_NUMBER_MARKS).translate(_PERSIAN_DIGITS)


def fa_percent(value: object, decimals: int = 1) -> str:
    """درصد با ارقام فارسی و علامت «٪»."""
    if value is None or value == "":
        return "—"
    try:
        float(value)
    except (TypeError, ValueError):
        return fa_digits(value)
    return f"{fa_number(value, decimals)}٪"


# ---------------------------------------------------------------------------
# پاک‌سازی و تشخیص نوع سلول
# ---------------------------------------------------------------------------
def clean_cell(value: object) -> str:
    """مقدار یک سلول/خط را به رشته‌ی تمیزشده‌ی نرمال‌شده تبدیل می‌کند.

    آرتیفکت‌های ``(cid:NN)`` که از فونت‌های جاسازی‌شده در PDF می‌آیند حذف
    می‌شوند؛ در غیر این صورت این آشغال‌ها وارد عنوان ردیف/ستون و در نتیجه وارد
    گزارش می‌شوند.
    """
    if value is None:
        return ""
    text = str(value)
    if _CID_RE.search(text):
        text = _CID_RE.sub("", text)
    if not _PERSIAN_LETTER_RE.search(text):
        # متن بدون حرف فارسی را نرمال‌ساز پروژه دست‌نخورده رها می‌کند (فقط فاصله‌ها).
        return _collapse_spaces(text)
    return normalize_persian_text(text)


def _collapse_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_key(text: object) -> str:
    """کلید مقایسه: نرمال‌شده، بدون فاصله‌ی اضافی، برای تطبیق‌های غیرحساس."""
    return re.sub(r"\s+", "", clean_cell(text)).strip()


def is_blank(text: object) -> bool:
    stripped = clean_cell(text)
    return stripped == "" or stripped in {"-", "--", "—", "_", "ـ", "..."}


def has_persian(text: object) -> bool:
    return bool(_PERSIAN_LETTER_RE.search(clean_cell(text)))


def is_mostly_numeric(text: object) -> bool:
    """آیا سلول عملاً عدد است (و در نتیجه هرگز نباید «آینه‌ای» تلقی شود)؟

    ارقام هیچ‌گاه معکوس نمی‌شوند (راست‌چین‌بودن متن آن‌ها را جابه‌جا نمی‌کند)، به
    همین دلیل تبدیل معکوس روی سلول عددی همیشه خرابی است.
    """
    cleaned = clean_cell(text)
    if not cleaned:
        return False
    if not any(ch.isdigit() for ch in cleaned):
        return False
    meaningful = [ch for ch in cleaned if not ch.isspace()]
    if not meaningful:
        return False
    allowed = [ch for ch in meaningful if not _NUMERIC_ALLOWED_RE.search(ch)]
    return len(allowed) / len(meaningful) >= 0.6


def parse_number(value: object) -> Optional[float]:
    """مقدار یک سلول را به عدد تبدیل می‌کند؛ اگر عدد نباشد ``None``.

    قواعد مهم (مطابق اصل ۲ پرامپت مرجع):
      * «-» / «—» / سلول خالی یعنی «مقداری ثبت نشده» → ``None`` (نه صفر).
      * عدد منفی حسابداری داخل پرانتز، ``(1234)`` → ``-1234``.
      * جداکننده‌ی هزارگان فارسی/لاتین حذف می‌شود، اعشار فارسی («٫») به نقطه.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, Real):
        number = float(value)
        return None if number != number else number  # NaN → None

    text = clean_cell(value)
    if not text or text in {"-", "--", "—", "_", "ـ"}:
        return None

    negative = False
    stripped = text.strip()
    if stripped.startswith("(") and stripped.endswith(")"):
        negative = True
        stripped = stripped[1:-1]

    digits_only = re.sub(r"[^\d.\-+]", "", stripped.replace(",", ""))
    if digits_only in {"", "-", "+", ".", "-.", "+."}:
        return None
    try:
        number = float(digits_only)
    except ValueError:
        return None
    return -number if negative else number


def detect_years(text: object) -> list[str]:
    """سال‌های شمسی (و در نبود آن میلادی) که در متن ظاهر شده‌اند.

    سال هرگز در منطق سامانه hard-code نمی‌شود؛ از همین تشخیص (یا از ورودی
    کاربر) خوانده می‌شود.
    """
    cleaned = clean_cell(text)
    if not cleaned:
        return []
    years = _YEAR_RE.findall(cleaned)
    if years:
        return years
    return _GREGORIAN_YEAR_RE.findall(cleaned)


def dominant_year(text: object) -> Optional[str]:
    """پرکاربردترین سال سند (سال شمسی) -- یا ``None`` اگر سالی شناسایی نشود."""
    years = detect_years(text)
    if not years:
        return None
    counts: dict[str, int] = {}
    for year in years:
        counts[year] = counts.get(year, 0) + 1
    # در تساوی، بزرگ‌ترین سال (سال جاری) انتخاب می‌شود.
    return max(counts.items(), key=lambda item: (item[1], item[0]))[0]


def detect_unit(text: object) -> Optional[str]:
    """واحد مبالغ سند («میلیون ریال»، «هزار ریال» و ...) در صورت ذکر صریح."""
    cleaned = clean_cell(text)
    if not cleaned:
        return None
    for unit in UNIT_CANDIDATES:
        if normalize_key(unit) in normalize_key(cleaned):
            return unit
    return None


# ---------------------------------------------------------------------------
# تطبیق معنایی (fuzzy)
# ---------------------------------------------------------------------------
def match_ratio(keyword: object, text: object) -> float:
    """میزان شباهت یک عبارت کلیدی به یک متن (۰ تا ۱۰۰) با همان روش پروژه.

    ``partial_ratio`` برای «حضور عبارت کلیدی در متن بلند» مناسب است، اما در متن
    کوتاه (سلول) به اشتباه بالا می‌رود؛ به همین دلیل برای متن‌های کوتاه از
    ``token_set_ratio`` استفاده می‌شود که ترتیب و تکرار کلمات را نادیده می‌گیرد.
    """
    left = normalize_key(keyword)
    right = normalize_key(text)
    if not left or not right:
        return 0.0
    if len(right) <= 24:
        return float(max(fuzz.token_set_ratio(left, right), fuzz.ratio(left, right)))
    return float(max(fuzz.partial_ratio(left, right), fuzz.token_set_ratio(left, right)))


def keyword_hits(text: object, keywords: Sequence[str], *, cutoff: float = 82.0) -> int:
    """تعداد عبارت‌های کلیدی که در متن (به‌صورت فازی) یافت می‌شوند."""
    return sum(1 for keyword in keywords if match_ratio(keyword, text) >= cutoff)


# ---------------------------------------------------------------------------
# تشخیص و رفع آینه‌ای‌شدن متن استخراج‌شده از PDF
# ---------------------------------------------------------------------------
def unmirror(text: object) -> str:
    """متن آینه‌ای را به ترتیب منطقی برمی‌گرداند (معکوس نویسه‌به‌نویسه).

    فقط روی بخش‌های غیرعددی اعمال می‌شود؛ ``decide_mirroring`` پیش از آن
    تصمیم می‌گیرد که کل سند آینه‌ای است یا نه.
    """
    cleaned = clean_cell(text)
    return cleaned[::-1]


def decide_mirroring(texts: Iterable[object], keywords: Sequence[str], *, margin: float = 1.4) -> bool:
    """آیا متن‌های استخراج‌شده (یک سند PDF) آینه‌ای هستند؟

    خروجی PDF چاپ‌شده از اکسل بسته به سازنده/فونت می‌تواند حروف فارسی را در
    ترتیب معکوس بدهد. به‌جای حدس‌زدن با یک خط یا نویسه‌های «کمکی»، امتیاز
    عبارت‌های کلیدی دامنه‌ای روی کل سند در حالت عادی و در حالت معکوس مقایسه
    می‌شود و تنها وقتی «معکوس» به‌طور معناداری برنده شود، معکوس‌سازی اعمال
    می‌گردد. سلول‌های عددی در این ارزیابی شرکت نمی‌کنند (معکوس‌شان همیشه
    خراب‌کننده است).
    """
    as_is = 0
    mirrored = 0
    for text in texts:
        if is_mostly_numeric(text) or not has_persian(text):
            continue
        cleaned = clean_cell(text)
        if len(cleaned) < 4:
            continue
        as_is += keyword_hits(cleaned, keywords)
        mirrored += keyword_hits(unmirror(cleaned), keywords)
    if mirrored < 2:
        return False
    return mirrored >= as_is * margin + 1


def repair_mirrored(text: object, *, mirrored: bool) -> str:
    """در صورت آینه‌ای‌بودن سند، سلول‌های متنی را برمی‌گرداند (سلول عددی دست‌نخورده)."""
    if not mirrored:
        return clean_cell(text)
    if is_mostly_numeric(text) or not has_persian(text):
        return clean_cell(text)
    return unmirror(text)
