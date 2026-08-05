from typing import List, Dict, Any, Optional, Tuple
import re
import concurrent.futures
import pandas as pd

from excel_loader import (
    load_all_sheets_to_memory,
    normalize_persian_text,
    normalize_sheet_name,
)
from auto_detect import detect_regions, detect_hierarchy_cols

DELIMITER = " _ "


def reconstruct_headers(df: pd.DataFrame, header_rows: List[int], delimiter: str = DELIMITER) -> List[str]:
    new_headers: List[str] = []
    num_cols = df.shape[1]

    for col_idx in range(num_cols):
        col_header_values: List[str] = []
        for r in header_rows:
            val = df.iloc[r, col_idx]
            if pd.notna(val):
                val_str = str(val).strip()
                if val_str:
                    col_header_values.append(val_str)

        deduplicated_values: List[str] = []
        for val in col_header_values:
            if not deduplicated_values or val != deduplicated_values[-1]:
                deduplicated_values.append(val)

        if deduplicated_values:
            new_headers.append(delimiter.join(deduplicated_values))
        else:
            new_headers.append(f"Unnamed_{col_idx}")

    return new_headers


def extract_title(df: pd.DataFrame, title_rows: List[int]) -> str:
    """متن ردیف‌های عنوان (نام سازمان، عنوان صورت مالی، تاریخ) را جمع می‌کند."""
    lines: List[str] = []
    for r in title_rows:
        row_vals = [
            str(v).strip() for v in df.iloc[r] if pd.notna(v) and str(v).strip() != ""
        ]
        seen = set()
        for v in row_vals:
            if v not in seen:
                seen.add(v)
                lines.append(v)
    return normalize_persian_text(" | ".join(lines))

def build_combined_index(
    clean_df: pd.DataFrame,
    hierarchy_cols_indices: List[int],
    delimiter: str = DELIMITER,
    fallback_label: str = "جمع کل (1)",
) -> List[str]:
    """
    برای هر ردیف، مقادیر ستون‌های سلسله‌مراتبی (شرح) را به هم می‌چسباند تا ایندکس بسازد.
    اگر ستون‌های شرح برای یک ردیف خالی باشند ولی بقیه‌ی ستون‌ها مقدار داشته باشند
    (مثلا ردیف «جمع کل» که فقط عدد جمع دارد و برچسب متنی جلوش نوشته نشده)،
    به‌جای رشته‌ی خالی (که باعث حذف کل ردیف می‌شد) از fallback_label استفاده می‌شود.
    """
    other_cols = [i for i in range(clean_df.shape[1]) if i not in hierarchy_cols_indices]
    combined_index: List[str] = []
    for _, row in clean_df.iterrows():
        seen = set()
        parts: List[str] = []
        for idx in hierarchy_cols_indices:
            val = row.iloc[idx]
            if pd.notna(val):
                val_str = str(val).strip()
                if val_str and val_str not in seen:
                    seen.add(val_str)
                    parts.append(val_str)
        label = delimiter.join(parts)
        if not label:
            has_other_data = any(pd.notna(row.iloc[i]) for i in other_cols)
            if has_other_data:
                label = fallback_label
        combined_index.append(label)
    return combined_index


def process_sheet(df: pd.DataFrame, delimiter: str = DELIMITER) -> Dict[str, Any]:
    regions = detect_regions(df)
    header_rows = regions["header_rows"]
    data_start = regions["data_start"]
    data_end = regions["data_end"]
    title_rows = regions["title_rows"]

    title = extract_title(df, title_rows) if title_rows else ""

    if data_start is None:
        # هیچ ردیف داده‌ی عددی واقعی در این شیت پیدا نشد
        return {
            "data": pd.DataFrame(),
            "title": title,
            "header_rows": header_rows,
            "data_start": None,
            "hierarchy_cols_indices": [],
        }

    headers = reconstruct_headers(df, header_rows, delimiter=delimiter)
    hierarchy_cols_indices = detect_hierarchy_cols(df, data_start, data_end)

    if data_end == -1:
        clean_df = df.iloc[data_start:].copy()
    else:
        clean_df = df.iloc[data_start:data_end].copy()
    clean_df.columns = headers

    combined_index = build_combined_index(clean_df, hierarchy_cols_indices, delimiter=delimiter)

    clean_df.index = combined_index
    clean_df = clean_df[clean_df.index.str.strip() != ""]

    cols_to_keep = [col for i, col in enumerate(clean_df.columns) if i not in hierarchy_cols_indices]
    clean_df = clean_df[cols_to_keep]

    unnamed_cols = [
        col for col in clean_df.columns
        if str(col) == "Unnamed" or str(col).startswith("Unnamed_") or str(col).startswith("Unnamed:")
    ]
    clean_df.drop(columns=unnamed_cols, inplace=True, errors="ignore")

    # حذف ردیف‌ها/ستون‌هایی که کاملا خالی‌اند
    clean_df = clean_df.dropna(axis=0, how="all").dropna(axis=1, how="all")

    return {
        "data": clean_df,
        "title": title,
        "header_rows": header_rows,
        "data_start": data_start,
        "hierarchy_cols_indices": hierarchy_cols_indices,
    }


# پیکربندی دستی: برای شیت‌هایی که تشخیص خودکار به‌درستی کار نکرد.
# این‌ها همان نقش FORMS_PARAM قبلی شما را دارند؛ چون این ۲ شیت یک بلوک عنوان چندردیفی
# دارند که یک ردیفِ تک‌سلولی («درآمدها»/«منابع») بلافاصله قبل از داده می‌آید و
# تشخیص خودکار را گول می‌زند.
MANUAL_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "ع": {  # صورت تغییرات در وضعیت مالی
        "header_rows": [4, 5],
        "data_start": 6,
        "data_end": -1,
        "hierarchy_cols_indices": [0],
    },
    "تفریغ": {  # صورت مقایسه بودجه و عملکرد
        "header_rows": [4, 5, 6],
        "data_start": 7,
        "data_end": -1,
        "hierarchy_cols_indices": [0],
    },
}

def _extract_block(df: pd.DataFrame, label_col: int, note_col: int, y1_col: int, y2_col: int,
                    header_rows: List[int], data_start: int, data_end: int,
                    y1_label: str, y2_label: str) -> pd.DataFrame:
    """یک بلوک ستونی مستقل (مثلا فقط سمت «دارایی‌ها») را به DataFrame تمیز تبدیل می‌کند."""
    rows = []
    index = []
    for r in range(data_start, data_end):
        label = df.iloc[r, label_col]
        if pd.isna(label) or str(label).strip() == "":
            continue
        note = df.iloc[r, note_col] if note_col is not None else None
        y1 = df.iloc[r, y1_col] if y1_col is not None else None
        y2 = df.iloc[r, y2_col] if y2_col is not None else None
        index.append(str(label).strip())
        rows.append({"یادداشت": note, y1_label: y1, y2_label: y2})
    return pd.DataFrame(rows, index=index)


def process_balance_sheet(df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """
    شیت «ت» (صورت وضعیت مالی) برخلاف بقیه، دو بلوک کاملا مستقل کنار هم دارد:
    سمت راست = دارایی‌ها (ستون‌های 0,2,4,6)، سمت چپ = بدهی‌ها و ارزش خالص
    (ستون‌های 8,10,12,14). ردیف‌های هدر (4 و 5) و محدوده‌ی داده (6 تا 20) بر
    اساس بازرسی مستقیم فایل واقعی مشخص شده‌اند.

    خروجی: دیکشنری با دو DataFrame:
      {'دارایی‌ها': df1, 'بدهی‌ها و ارزش خالص': df2}
    """
    data_start, data_end = 6, 21  # ردیف 21 (0-indexed) خالی است؛ ردیف 20 = جمع نهایی

    assets = _extract_block(
        df, label_col=0, note_col=2, y1_col=4, y2_col=6,
        header_rows=[4, 5], data_start=data_start, data_end=data_end,
        y1_label="1398/12/29", y2_label="1397/12/29",
    )
    liabilities = _extract_block(
        df, label_col=8, note_col=10, y1_col=12, y2_col=14,
        header_rows=[4, 5], data_start=data_start, data_end=data_end,
        y1_label="1398/12/29", y2_label="1397/12/29",
    )
    return {"دارایی‌ها": assets, "بدهی‌ها و ارزش خالص": liabilities}


# شیت‌هایی که ساختار دو-بلوکی (راست/چپ) دارند و باید با process_balance_sheet پردازش شوند
TWO_BLOCK_SHEETS = {"ت"}

# شیت‌هایی که مثل «يادداشتها» ساختار چندبلوکی (چند یادداشت شماره‌دار پشت سر هم) دارند
NOTES_SPLIT_SHEETS = {"يادداشتها", "حمایت ها", "یادداشتها2"}


def process_sheet_with_overrides(sheet_name: str, df: pd.DataFrame, delimiter: str = DELIMITER) -> Dict[str, Any]:
    normalized = normalize_sheet_name(sheet_name)
    if normalized in MANUAL_OVERRIDES:
        ov = MANUAL_OVERRIDES[normalized]
        header_rows = ov["header_rows"]
        data_start = ov["data_start"]
        data_end = ov.get("data_end", -1)
        hierarchy_cols_indices = ov["hierarchy_cols_indices"]
        title_rows = list(range(0, header_rows[0]))

        headers = reconstruct_headers(df, header_rows, delimiter=delimiter)
        title = extract_title(df, title_rows)

        if data_end == -1:
            clean_df = df.iloc[data_start:].copy()
        else:
            clean_df = df.iloc[data_start:data_end].copy()
        clean_df.columns = headers

        combined_index = build_combined_index(clean_df, hierarchy_cols_indices, delimiter=delimiter)
        clean_df.index = combined_index
        clean_df = clean_df[clean_df.index.str.strip() != ""]

        cols_to_keep = [col for i, col in enumerate(clean_df.columns) if i not in hierarchy_cols_indices]
        clean_df = clean_df[cols_to_keep]
        unnamed_cols = [
            col for col in clean_df.columns
            if str(col) == "Unnamed" or str(col).startswith("Unnamed_") or str(col).startswith("Unnamed:")
        ]
        clean_df.drop(columns=unnamed_cols, inplace=True, errors="ignore")
        clean_df = clean_df.dropna(axis=0, how="all").dropna(axis=1, how="all")

        return {
            "data": clean_df,
            "title": title,
            "header_rows": header_rows,
            "data_start": data_start,
            "hierarchy_cols_indices": hierarchy_cols_indices,
        }

    return process_sheet(df, delimiter=delimiter)


NOTE_MARKER_RE = re.compile(r"^-?\d+(?:-\d+)*$")


def _block_full_text(block: pd.DataFrame) -> str:
    """وقتی بلوکی جدول عددی ندارد، همه‌ی متن آن (توضیح یادداشت) را برمی‌گرداند."""
    lines: List[str] = []
    for _, row in block.iterrows():
        seen = set()
        for v in row:
            if pd.notna(v) and str(v).strip() != "":
                s = str(v).strip()
                if s not in seen:
                    seen.add(s)
                    lines.append(s)
    return normalize_persian_text(" ".join(lines))


def process_notes_sheet(df: pd.DataFrame, delimiter: str = DELIMITER) -> Dict[str, Any]:
    """
    شیت «يادداشتها» یک جدول واحد نیست: دنباله‌ای از یادداشت‌های شماره‌دار
    (-4، -4-1، -4-1-1، -4-2، ...) پشت سر هم است که هرکدام هدر و جدول کوچک
    مستقل خودش را دارد (مثلا یادداشت / 1398 / ریال / 1397). این تابع اول
    شیت را بر اساس شماره یادداشت (ستون اول) به بلوک‌های جدا می‌شکند، بعد هر
    بلوک را با همان تشخیص خودکار (detect_regions) پردازش می‌کند.

    خروجی: دیکشنری {شماره_یادداشت: DataFrame یا str}
      - اگر یادداشت جدول عددی داشت -> DataFrame
      - اگر یادداشت فقط یک توضیح متنی بود (بدون جدول) -> رشته‌ی متن توضیح
      - اگر شماره‌ی یادداشتی تکراری بود (دو بار در شیت آمده)، کلید دوم با
        پسوند "__2" و ... ذخیره می‌شود تا هیچ‌کدام گم نشوند.
    """
    markers: List[int] = []
    for r in range(len(df)):
        v = df.iloc[r, 0]
        if pd.notna(v) and NOTE_MARKER_RE.match(str(v).strip()):
            markers.append(r)

    if not markers:
        return {}

    notes: Dict[str, Any] = {}
    for i, start in enumerate(markers):
        end = markers[i + 1] if i + 1 < len(markers) else len(df)
        note_id = str(df.iloc[start, 0]).strip()
        block = df.iloc[start:end].reset_index(drop=True)

        # اگر این شماره یادداشت قبلا دیده شده، کلید را با پسوند شماره‌گذاری کن
        final_id = note_id
        dup_count = 2
        while final_id in notes:
            final_id = f"{note_id}__{dup_count}"
            dup_count += 1

        # ردیف اول بلوک، شرح یادداشت است (نه هدر ستون)؛ از ردیف بعدی تشخیص خودکار را اجرا کن
        sub = block.copy()
        sub.iloc[0, 0] = None   # شماره یادداشت (مثلا "-4-1") پاک می‌شود
        sub.iloc[0, 1] = None   # جمله‌ی توضیحی پاک می‌شود
        if sub.dropna(how="all").empty:
            # کل محتوای یادداشت همان یک ردیفِ مارکر است (فقط متن، بدون جدول)
            text = _block_full_text(block)
            if text:
                notes[final_id] = text
            continue

        try:
            regions = detect_regions(sub, max_scan_rows=min(30, len(sub)))
            data_start = regions["data_start"]

            if data_start is None:
                # این یادداشت جدول عددی ندارد؛ فقط متن توضیحی است -> متن را نگه دار
                text = _block_full_text(block)
                if text:
                    notes[final_id] = text
                continue

            headers = reconstruct_headers(sub, regions["header_rows"], delimiter=delimiter)
            hierarchy_cols_indices = detect_hierarchy_cols(sub, data_start, regions["data_end"])

            data_end = regions["data_end"]
            clean_df = sub.iloc[data_start:].copy() if data_end == -1 else sub.iloc[data_start:data_end].copy()
            clean_df.columns = headers

            combined_index = build_combined_index(clean_df, hierarchy_cols_indices, delimiter=delimiter)
            clean_df.index = combined_index
            clean_df = clean_df[clean_df.index.str.strip() != ""]

            cols_to_keep = [c for i2, c in enumerate(clean_df.columns) if i2 not in hierarchy_cols_indices]
            clean_df = clean_df[cols_to_keep]
            unnamed = [c for c in clean_df.columns if str(c).startswith("Unnamed")]
            clean_df.drop(columns=unnamed, inplace=True, errors="ignore")
            clean_df = clean_df.dropna(axis=0, how="all").dropna(axis=1, how="all")

            if not clean_df.empty:
                notes[final_id] = clean_df
            else:
                text = _block_full_text(block)
                if text:
                    notes[final_id] = text
        except Exception:
            # اگر پردازش جدولی شکست خورد، حداقل متن خام یادداشت را نگه دار تا گم نشود
            text = _block_full_text(block)
            if text:
                notes[final_id] = text

    return notes


def _process_single_sheet(sheet_name: str, df: pd.DataFrame) -> Tuple[str, Any]:
    """تابع سطح-ماژول قابل pickle برای پردازش یک شیت؛ برای اجرای موازی لازم است."""
    normalized = normalize_sheet_name(sheet_name)
    if normalized in NOTES_SPLIT_SHEETS:
        # خروجی این شیت یک دیکشنری {شماره‌یادداشت: DataFrame/str} است، نه یک DataFrame
        return sheet_name, process_notes_sheet(df)
    elif normalized in TWO_BLOCK_SHEETS:
        # خروجی این شیت یک دیکشنری با دو DataFrame (راست/چپ) است
        return sheet_name, process_balance_sheet(df)
    else:
        return sheet_name, process_sheet_with_overrides(sheet_name, df)


def main(file_path: str, parallel: bool = True) -> Dict[str, Any]:
    raw_sheets = load_all_sheets_to_memory(file_path, parallel=parallel)
    result: Dict[str, Any] = {}

    if not parallel or len(raw_sheets) <= 1:
        for sheet_name, df in raw_sheets.items():
            try:
                _, processed = _process_single_sheet(sheet_name, df)
                result[sheet_name] = processed
            except Exception as e:
                print(f"خطا در پردازش شیت '{sheet_name}': {e}")
        return result

    try:
        with concurrent.futures.ProcessPoolExecutor() as executor:
            futures = {
                executor.submit(_process_single_sheet, sheet_name, df): sheet_name
                for sheet_name, df in raw_sheets.items()
            }
            for future in concurrent.futures.as_completed(futures):
                sheet_name = futures[future]
                try:
                    _, processed = future.result()
                    result[sheet_name] = processed
                except Exception as e:
                    print(f"خطا در پردازش شیت '{sheet_name}': {e}")
    except Exception as e:
        print(f"موازی‌سازی پردازش شیت‌ها شکست خورد، بازگشت به حالت سریالی: {e}")
        result = {}
        for sheet_name, df in raw_sheets.items():
            try:
                _, processed = _process_single_sheet(sheet_name, df)
                result[sheet_name] = processed
            except Exception as e2:
                print(f"خطا در پردازش شیت '{sheet_name}': {e2}")

    return result


if __name__ == "__main__":
    import time as time
    start=time.time()
    path = "extraction_script/صورتهاي مالي 1398.xlsx"
    results = main(path)
    final =time.time() - start
    print(final)
    for name, r in results.items():
        print("=" * 80)
        normalized = normalize_sheet_name(name)
        if normalized in NOTES_SPLIT_SHEETS:
            print(f"شیت: {name}  (چندجدولی؛ {len(r)} یادداشت پیدا شد)")
            for note_id, note_val in list(r.items())[:3]:
                if isinstance(note_val, pd.DataFrame):
                    print(f"  -- یادداشت {note_id} (جدول) -- shape={note_val.shape}")
                    print(note_val.head(3))
                else:
                    print(f"  -- یادداشت {note_id} (متن) -- {note_val[:120]}")
            continue
        if normalized in TWO_BLOCK_SHEETS:
            print(f"شیت: {name}  (دو بلوک)")
            for block_name, block_df in r.items():
                print(f"  -- {block_name} -- shape={block_df.shape}")
                print(block_df.head(5))
            continue
        print(f"شیت: {name}   |   عنوان: {r['title'][:80]}")
        print(f"header_rows={r['header_rows']} data_start={r['data_start']} hierarchy_cols={r['hierarchy_cols_indices']}")
        print(r["data"].head(5))
    
