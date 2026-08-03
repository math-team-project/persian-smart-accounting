"""
تشخیص خودکار ناحیه‌ی عنوان / هدر / داده در هر شیت یا هر بلوکِ یادداشت.

نسخه ۲: نسخه اول با دو مشکل رایج در این فایل مواجه می‌شد:
  1) سال‌ها (1398 / 1397) گاهی به‌صورت عدد واقعی (نه رشته) ذخیره شده‌اند، پس
     ردیف هدر را به اشتباه «ردیف داده» تشخیص می‌داد.
  2) بین بلوکِ هدر و داده، اغلب یک جمله توضیحی تک‌سلولی می‌آید (مثلا
     «... به شرح زیر قابل تفکیک است:») که اسکن به عقب را زودتر از موعد متوقف می‌کرد
     و هدر واقعی را از دست می‌داد.

راه‌حل: هر ردیف را به یکی از ۴ دسته «داده / هدر / توضیح تک‌جمله‌ای / خالی»
دسته‌بندی می‌کنیم؛ فقط اعداد «بزرگ» (>= LARGE_THRESHOLD) نشانه‌ی ردیف داده
هستند (سال و شماره یادداشت اعداد کوچک‌اند)، و ردیف‌های توضیحیِ تک‌سلولی
نادیده گرفته می‌شوند ولی اسکن را متوقف نمی‌کنند.
"""
from typing import List, Dict, Any, Optional
import pandas as pd
import numbers

LARGE_THRESHOLD = 5000          # اعداد با قدر مطلق بزرگ‌تر از این، «داده مالی واقعی» فرض می‌شوند
DESCRIPTION_TEXT_LEN = 25       # جمله‌ی توضیحی تک‌سلولی معمولا از این بلندتر است


def _is_number(v: Any) -> bool:
    return isinstance(v, numbers.Number) and not isinstance(v, bool)


def _row_values(row: pd.Series) -> list:
    return [v for v in row if pd.notna(v) and str(v).strip() != ""]


def _row_stats(row: pd.Series) -> Dict[str, Any]:
    non_null = _row_values(row)
    numeric_vals = [v for v in non_null if _is_number(v)]
    return {
        "non_null": len(non_null),
        "values": non_null,
        "numeric": numeric_vals,
        "has_large": any(abs(v) >= LARGE_THRESHOLD for v in numeric_vals),
    }


def _classify_row(row: pd.Series) -> str:
    stats = _row_stats(row)
    if stats["non_null"] == 0:
        return "empty"
    if stats["has_large"]:
        return "data"
    if stats["non_null"] == 1:
        val = stats["values"][0]
        if isinstance(val, str) and len(val.strip()) > DESCRIPTION_TEXT_LEN:
            return "description"
        return "header"
    return "header"


def detect_regions(df: pd.DataFrame, max_scan_rows: int = 60) -> Dict[str, Any]:
    """
    خروجی:
      title_rows: ردیف‌های عنوان/نام سازمان/تاریخ (بالای جدول)
      header_rows: ردیف(های) عنوان ستون‌ها
      data_start: اندیس شروع داده (یا None اگر بلوک اصلا جدول عددی نداشت)
      data_end: اندیس پایان داده (exclusive) یا -1 برای تا انتهای شیت
    """
    n_rows = min(max_scan_rows, len(df))

    data_start: Optional[int] = None
    for r in range(n_rows):
        if _classify_row(df.iloc[r]) == "data":
            data_start = r
            break

    if data_start is None:
        # نسخه‌ی ملایم‌تر: شیت‌هایی مثل فهرست/فهرست مطالب اصلا عدد مالی بزرگ ندارند
        # (فقط شماره صفحه)؛ اینجا هر ردیفی با حداقل ۲ مقدار عددی را «داده» می‌گیریم
        for r in range(n_rows):
            stats = _row_stats(df.iloc[r])
            if len(stats["numeric"]) >= 2:
                data_start = r
                break

    if data_start is None:
        # ملایم‌ترین حالت: شیت‌های خیلی ساده (مثل فهرست مطالب) که فقط یک عدد
        # در هر ردیف دارند (مثلا شماره صفحه) را هم به عنوان داده در نظر بگیر
        for r in range(n_rows):
            stats = _row_stats(df.iloc[r])
            if len(stats["numeric"]) >= 1 and stats["non_null"] >= 2:
                data_start = r
                break

    if data_start is None:
        return {"title_rows": [], "header_rows": [], "data_start": None, "data_end": -1}

    header_rows: List[int] = []
    r = data_start - 1
    while r >= 0 and len(header_rows) < 6:
        cls = _classify_row(df.iloc[r])
        if cls == "data" or cls == "empty":
            break
        elif cls == "description":
            r -= 1
            continue
        else:  # header
            header_rows.insert(0, r)
            r -= 1

    title_rows = list(range(0, header_rows[0])) if header_rows else list(range(0, data_start))

    return {
        "title_rows": title_rows,
        "header_rows": header_rows,
        "data_start": data_start,
        "data_end": -1,
    }


def detect_hierarchy_cols(df: pd.DataFrame, data_start: int, data_end: int, max_cols: int = 4) -> List[int]:
    """
    ستون‌های سمت چپ که عمدتا متنی هستند (شرح/عنوان ردیف) را به عنوان
    ستون‌های سلسله‌مراتبی (index) شناسایی می‌کند.
    """
    end = len(df) if data_end == -1 else data_end
    block = df.iloc[data_start:end]
    hierarchy_cols: List[int] = []

    for c in range(min(max_cols, df.shape[1])):
        col = block.iloc[:, c]
        non_null = col.dropna()
        if len(non_null) == 0:
            continue
        text_ratio = sum(not _is_number(v) for v in non_null) / len(non_null)
        if text_ratio >= 0.6:
            hierarchy_cols.append(c)
        else:
            break

    if not hierarchy_cols:
        hierarchy_cols = [0]

    return hierarchy_cols
