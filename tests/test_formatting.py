"""تست‌های قالب‌بندی نمایشی فارسی (تاریخ شمسی و ارقام فارسی)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from api.utils.formatting import (
    fa_date,
    fa_datetime,
    fa_number,
    fa_percent,
    gregorian_to_jalali,
    to_persian_digits,
)


def test_jalali_conversion_anchors():
    # ۲۲ بهمن ۱۳۵۷ -- انقلاب اسلامی
    assert gregorian_to_jalali(1979, 2, 11) == (1357, 11, 22)
    # نوروز ۱۴۰۳
    assert gregorian_to_jalali(2024, 3, 20) == (1403, 1, 1)
    # آخرین روز سال ۱۴۰۲
    assert gregorian_to_jalali(2024, 3, 19) == (1402, 12, 29)
    # یک تاریخ میانی (برابر ۲۶ شهریور ۱۴۰۵)
    assert gregorian_to_jalali(2026, 9, 17) == (1405, 6, 26)


def test_persian_digits_and_numbers():
    assert to_persian_digits("1234") == "۱۲۳۴"
    assert fa_number(1234) == "۱٬۲۳۴"
    assert fa_number(64.1025, decimals=1) == "۶۴٫۱"
    assert fa_percent(64.1025) == "۶۴٪"
    # مقدار غایب/نامعتبر نباید استثنا بدهد (فهرست تاریخچه ممکن است خلاصه نداشته باشد)
    assert fa_number(None) == "—"
    assert fa_percent("") == "—"
    assert fa_number("متن") == "متن"


def test_fa_date_and_datetime_render_farsi():
    # ۲۶ شهریور ۱۴۰۵
    naive_utc = datetime(2026, 9, 17, 13, 47)
    assert fa_date(naive_utc).endswith("شهریور ۱۴۰۵")
    assert fa_date(naive_utc).startswith("۲۶")
    assert "۱۴۰۵" in fa_datetime(naive_utc)
    assert fa_date(None) == "—"


def test_naive_database_timestamps_are_treated_as_utc():
    """SQLite زمان را بدون منطقه‌زمانی برمی‌گرداند؛ نمایش باید همان لحظه‌ی واقعی باشد."""
    aware_utc = datetime(2026, 9, 17, 13, 47, tzinfo=timezone.utc)
    naive_utc = aware_utc.replace(tzinfo=None)

    assert fa_datetime(naive_utc) == fa_datetime(aware_utc)

    # و صریحاً: اختلاف ساعت نمایش‌داده‌شده با UTC برابر افست محلی است
    local = naive_utc.replace(tzinfo=timezone.utc).astimezone()
    expected_clock = f"{local.hour:02d}:{local.minute:02d}"
    from api.utils.formatting import to_persian_digits

    assert to_persian_digits(expected_clock) in fa_datetime(naive_utc)


def test_fa_datetime_handles_other_timezones():
    tehran_ish = datetime(2026, 9, 17, 17, 47, tzinfo=timezone(timedelta(hours=3, minutes=30)))
    assert fa_datetime(tehran_ish) == fa_datetime(datetime(2026, 9, 17, 14, 17, tzinfo=timezone.utc))
