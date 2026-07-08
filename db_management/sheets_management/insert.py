import json
import os
import re
import sys

import pandas as pd
import psycopg2
from psycopg2 import extras

# اضافه کردن ریشه‌ی پروژه به sys.path تا اجرای مستقیم این فایل هم کار کند
# (وقتی این اسکریپت مستقیم اجرا می‌شود، فقط پوشه‌ی خودش به sys.path اضافه می‌شود،
# نه ریشه‌ی پروژه که extraction_script در آن قرار دارد)
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_EXTRACTION_SCRIPT_DIR = os.path.join(_PROJECT_ROOT, "extraction_script")
for _path in (_PROJECT_ROOT, _EXTRACTION_SCRIPT_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from extraction_script.extraction_multicore_with_bypass_ram import main

# این دو رشته دقیقا همانی هستند که به main() پاس داده می‌شوند
# و همان مقداری هستند که در ستون budget_type جدول budget_records ذخیره می‌شوند
BUDGET_TYPE_NORMAL = "تفضیلی"
BUDGET_TYPE_REVISED = "اصلاحیه"

# اطلاعات اتصال به دیتابیس جدیدی که ساختیم
DB_NAME = "smart_acounting_db"
DB_USER = "postgres"
DB_PASSWORD = "90908080"  # پسورد
DB_HOST = "localhost"
DB_PORT = "5432"


def get_connection():
    return psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
    )


def ensure_unique_constraints() -> None:
    """
    برای جلوگیری از درج تکراری داده‌ها، این تابع مطمئن می‌شود که
    محدودیت‌های یکتایی لازم روی meta_data و budget_records وجود دارند.
    اگر قبلا ساخته شده باشند، خطای مربوطه نادیده گرفته می‌شود.
    """
    conn = get_connection()
    conn.autocommit = True
    cur = conn.cursor()
    try:
        try:
            cur.execute(
                """
                ALTER TABLE meta_data
                ADD CONSTRAINT meta_data_form_id_unique UNIQUE (form_id)
                """
            )
        except (psycopg2.errors.DuplicateObject, psycopg2.errors.DuplicateTable):
            pass

        # اگر ستون/محدودیت occurrence از تلاش قبلی باقی مانده باشد، آن را برمی‌گردانیم
        # به حالت ساده: یک ردیف یکتا برای هر (form_id, budget_type, item, column_name)
        cur.execute(
            """
            ALTER TABLE budget_records
            DROP CONSTRAINT IF EXISTS budget_records_unique
            """
        )
        cur.execute(
            """
            ALTER TABLE budget_records
            DROP COLUMN IF EXISTS occurrence
            """
        )
        # اگر از اجرای قبلی (رویکرد occurrence) ردیف‌های تکراری واقعی در جدول مانده،
        # فقط یک نسخه از هر (form_id, budget_type, item, column_name) نگه داشته می‌شود
        cur.execute(
            """
            DELETE FROM budget_records a
            USING budget_records b
            WHERE a.id < b.id
              AND a.form_id = b.form_id
              AND a.budget_type = b.budget_type
              AND a.item = b.item
              AND a.column_name = b.column_name
            """
        )

        try:
            cur.execute(
                """
                ALTER TABLE budget_records
                ADD CONSTRAINT budget_records_unique
                UNIQUE (form_id, budget_type, item, column_name)
                """
            )
        except (psycopg2.errors.DuplicateObject, psycopg2.errors.DuplicateTable):
            pass
    finally:
        cur.close()
        conn.close()


def sanitize_form_id(form_key: str, header: dict) -> str:
    """
    یک form_id یکتا و پایدار می‌سازد که هم برای داده‌های تفصیلی
    و هم اصلاحیه‌ی همان فرم، یکسان باشد (تا هر دو به یک ردیف forms اشاره کنند).
    """
    form_number = header.get("form_number") or form_key
    budget_year = header.get("budget_year") or "NA"
    device_id = header.get("device_id") or "NA"

    raw = f"{form_number}_{budget_year}_{device_id}"
    raw = re.sub(r"\s+", "", raw)
    # فقط کاراکترهای امن را نگه می‌داریم تا از مشکلات احتمالی جلوگیری شود
    raw = re.sub(r"[^0-9A-Za-zآ-ی_\-]", "", raw)
    return raw[:50]


def insert_form(cur, form_id: str, header: dict) -> None:
    cur.execute(
        """
        INSERT INTO forms (form_id, org_name, device_id, budget_year, form_name, form_title)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (form_id) DO NOTHING
        """,
        (
            form_id,
            header.get("org_name") or "نامشخص",
            header.get("device_id") or "NA",
            header.get("budget_year") or 0,
            header.get("form_number") or "",
            header.get("form_title"),
        ),
    )


def insert_meta_data(cur, form_id: str, metadata) -> None:
    # metadata یک set از رشته‌هاست؛ برای ذخیره در JSONB باید به لیست تبدیل شود
    attributes = sorted(metadata) if metadata else []
    cur.execute(
        """
        INSERT INTO meta_data (form_id, attributes)
        VALUES (%s, %s)
        ON CONFLICT (form_id) DO UPDATE
        SET attributes = EXCLUDED.attributes
        """,
        (form_id, extras.Json(attributes)),
    )


def insert_budget_records(
    cur, form_id: str, budget_type: str, df: pd.DataFrame
) -> None:
    if df is None or df.empty:
        return

    # اگر (item, column_name) در دیتافریم تکرار باشد (مثلا ردیف‌های خالی/جایگذار
    # در شیت اکسل با همان برچسب تکراری)، فقط یک نسخه از آن ثبت می‌شود
    # تا هم وجود آن ردیف/ستون در فرم ثبت شود و هم خطای یکتایی رخ ندهد
    records_by_key = {}
    for item, row in df.iterrows():
        for column_name, value in row.items():
            numeric_value = None
            descriptive_value = None
            is_numeric = True

            if pd.isna(value):
                # سلول خالی/NaN هم ثبت می‌شود تا وجود آن ردیف/ستون در فرم مشخص باشد
                is_numeric = False
            else:
                try:
                    numeric_value = float(value)
                except (ValueError, TypeError):
                    is_numeric = False
                    descriptive_value = str(value).strip()

            key = (form_id, budget_type, str(item), str(column_name))
            records_by_key[key] = key + (numeric_value, descriptive_value, is_numeric)

    records = list(records_by_key.values())

    if not records:
        return

    extras.execute_values(
        cur,
        """
        INSERT INTO budget_records
            (form_id, budget_type, item, column_name, numeric_value, descriptive_value, is_numeric)
        VALUES %s
        ON CONFLICT (form_id, budget_type, item, column_name) DO UPDATE
        SET numeric_value = EXCLUDED.numeric_value,
            descriptive_value = EXCLUDED.descriptive_value,
            is_numeric = EXCLUDED.is_numeric
        """,
        records,
    )


def process_form_result(
    cur, form_key: str, result: dict, budget_type: str, inserted_forms: set
) -> str:
    header = result.get("header") or {}
    metadata = result.get("metadata") or set()

    form_id = sanitize_form_id(form_key, header)

    if form_id not in inserted_forms:
        insert_form(cur, form_id, header)
        insert_meta_data(cur, form_id, metadata)
        inserted_forms.add(form_id)

    # فرم 3 دیتافریم data ندارد (فقط metadata و header دارد)
    df = result.get("data")
    if df is not None:
        insert_budget_records(cur, form_id, budget_type, df)

    return form_id


def insert_all_data(normal_data: dict, revised_data_: dict) -> None:
    ensure_unique_constraints()

    conn = get_connection()
    conn.set_client_encoding("UTF8")  # تضمین ارتباط UTF-8 بین پایتون و دیتابیس
    cur = conn.cursor()
    inserted_forms: set = set()

    try:
        for form_key, result in normal_data.items():
            process_form_result(
                cur, form_key, result, BUDGET_TYPE_NORMAL, inserted_forms
            )

        for form_key, result in revised_data_.items():
            process_form_result(
                cur, form_key, result, BUDGET_TYPE_REVISED, inserted_forms
            )

        conn.commit()
        print("داده‌ها با موفقیت درج شدند.")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    # فراخوانی main() حتما باید داخل این بلاک باشد
    # (extraction_multicore_with_bypass_ram از ProcessPoolExecutor استفاده می‌کند و در ویندوز
    # بدون این گارد، هر پراسه فرزند، ماژول را از ابتدا اجرا می‌کند)
    data = main(budget_type=BUDGET_TYPE_NORMAL)
    revised_data = main(budget_type=BUDGET_TYPE_REVISED)

    insert_all_data(data, revised_data)
