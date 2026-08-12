import pandas as pd

file_path = "extraction_script/data/تراز 98.xlsx"  # مسیر فایل اکسل رو در صورت نیاز تغییر بده

# خواندن تمام شیت‌های فایل (به هر تعداد که باشن)
raw_sheets = pd.read_excel(file_path, sheet_name=None)  # دیکشنری: {نام شیت: دیتافریم}

# ساخت ساختار df["نام شیت"]["data"]
df = {sheet_name: {"data": sheet_df} for sheet_name, sheet_df in raw_sheets.items()}

# نمونه استفاده
for sheet_name in df:
    print(f"شیت: {sheet_name}  |  تعداد ردیف‌ها: {len(df[sheet_name]['data'])}")

