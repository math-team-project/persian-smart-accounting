"""قالب‌بندی نمایشی برای رابط فارسی: تاریخ شمسی و ارقام فارسی.

هیچ کتابخانه‌ی بیرونی‌ای اضافه نشده است: تبدیل میلادی→شمسی یک الگوریتم
استاندارد و کوتاه است و برای نمایش تاریخ در داشبورد کافی است (نیازی به محاسبه‌ی
دقیق لحظه‌ی تحویل سال یا تبدیل معکوس نیست).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

# جداکننده‌های فارسی: هزارگان «٬» (U+066C) و اعشار «٫» (U+066B) -- تا اعداد
# فارسی از نظر ظاهری هم درست باشند، نه فقط ارقامشان.
_PERSIAN_NUMBER_MARKS = str.maketrans({",": "٬", ".": "٫"})

_PERSIAN_MONTHS = (
    "فروردین",
    "اردیبهشت",
    "خرداد",
    "تیر",
    "مرداد",
    "شهریور",
    "مهر",
    "آبان",
    "آذر",
    "دی",
    "بهمن",
    "اسفند",
)


def to_persian_digits(value: Any) -> str:
    return str(value).translate(_PERSIAN_DIGITS)


def fa_number(value: Any, decimals: int = 0) -> str:
    """عدد را با جداکننده‌ی هزارگان و ارقام فارسی برمی‌گرداند (مناسب مقادیر آماری)."""
    if value is None or value == "":
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return to_persian_digits(value)
    if decimals > 0:
        text = f"{number:,.{decimals}f}"
    else:
        text = f"{number:,.0f}"
    return text.translate(_PERSIAN_NUMBER_MARKS).translate(_PERSIAN_DIGITS)


def fa_percent(value: Any, decimals: int = 0) -> str:
    if value is None or value == "":
        return "—"
    return f"{fa_number(value, decimals)}٪"


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    """تبدیل تاریخ میلادی به شمسی (الگوریتم استاندارد، بدون وابستگی بیرونی)."""
    g_d_m = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)
    gy2 = gy - 1600
    gm2 = gm - 1
    gd2 = gd - 1

    g_day_no = 365 * gy2 + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
    g_day_no += g_d_m[gm2] + gd2
    if gm > 2 and ((gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0):
        g_day_no += 1

    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053

    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461

    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365

    if j_day_no < 186:
        jm = 1 + j_day_no // 31
        jd = 1 + j_day_no % 31
    else:
        jm = 7 + (j_day_no - 186) // 30
        jd = 1 + (j_day_no - 186) % 30

    return jy, jm, jd


def _as_local(value: datetime) -> datetime:
    """تاریخ ذخیره‌شده در پایگاه‌داده را به وقت محلی سرور تبدیل می‌کند.

    همه‌ی زمان‌های پایگاه‌داده با ``api.db.models.utcnow`` (یعنی UTC آگاه از
    منطقه‌زمانی) نوشته می‌شوند، اما SQLite آن‌ها را بدون اطلاعات منطقه‌زمانی برمی‌گرداند؛
    پس مقدار naive را UTC در نظر می‌گیریم و بعد به وقت محلی تبدیل می‌کنیم -- وگرنه
    ساعت نمایش‌داده‌شده به اندازه‌ی اختلاف منطقه‌زمانی سرور با UTC جابه‌جا می‌شد.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone()


def fa_date(value: datetime | None) -> str:
    """تاریخ شمسی، مثل «۲۶ شهریور ۱۴۰۵»."""
    if value is None:
        return "—"
    local = _as_local(value)
    jy, jm, jd = gregorian_to_jalali(local.year, local.month, local.day)
    return f"{to_persian_digits(jd)} {_PERSIAN_MONTHS[jm - 1]} {to_persian_digits(jy)}"


def fa_datetime(value: datetime | None, *, with_time: bool = True) -> str:
    """تاریخ و ساعت شمسی، مثل «۲۶ شهریور ۱۴۰۵ — ۱۴:۳۲»."""
    if value is None:
        return "—"
    date_part = fa_date(value)
    if not with_time:
        return date_part
    local = _as_local(value)
    clock = f"{local.hour:02d}:{local.minute:02d}"
    return f"{date_part} — {to_persian_digits(clock)}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
