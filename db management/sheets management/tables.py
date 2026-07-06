import psycopg2
from psycopg2 import sql, OperationalError
import json

DB_NAME = "smart_acounting_db"
DB_USER = "postgres"
DB_PASSWORD = "90908080"      
DB_HOST = "localhost"
DB_PORT = "5432"

def create_table():
    """اتصال به دیتابیس جدید و ساختن جدول با فیلد JSONB"""
    conn = None
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT
        )
        conn.set_client_encoding('UTF8')  # تضمین ارتباط UTF-8 بین پایتون و دیتابیس
        cur = conn.cursor()
        
        # دستور CREATE TABLE با استفاده از نوع JSONB
        create_table_query = """
        CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
        CREATE EXTENSION IF NOT EXISTS "ltree";
        --todo: CREATE EXTENSION IF NOT EXISTS "vector";

        CREATE TABLE IF NOT EXISTS forms (
            form_id VARCHAR(50) PRIMARY KEY,
            org_name VARCHAR(255) NOT NULL,          -- نام سازمان
            device_id VARCHAR(50) NOT NULL,          -- شماره ردیف دستگاه (مثلاً 113527)
            budget_year INTEGER NOT NULL,            -- سال بودجه (1404 مثلا)
            form_name VARCHAR(20) NOT NULL,          -- نام فرم (مثلاً 'فرم 1')
            form_title TEXT,                         -- عنوان فرم (درصورت وجود)
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS meta_data (
            id SERIAL PRIMARY KEY,
            form_id VARCHAR(50) REFERENCES forms(form_id),
            attributes JSONB,                        -- اینجا داده‌های JSON ذخیره می‌شود
            note TEXT,                               -- توضیحات فرم (درصورت نیاز)
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS budget_records (
            id SERIAL PRIMARY KEY,
            form_id VARCHAR(50) REFERENCES forms(form_id),
            budget_type VARCHAR(50) NOT NULL,        -- 'تفصیلی' یا 'اصلاحیه'
            item TEXT,                               -- عنوان ردیف (مانده اعتبارسال قبل، ...)
            column_name TEXT,                        -- نام ستون ('اعتبار 1404', 'عملکرد 1403', ...) 
            numeric_value NUMERIC,                   -- مقدار عددی (در میلیون ریال) 
            Descriptive_value TEXT,                  -- مقدار غیر عددی (درصورتی که مقدار عددی نباشد)
            is_numeric BOOLEAN DEFAULT TRUE,         -- آیا مقدا عددی است یا توضیفی
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );

        --CREATE TABLE IF NOT EXISTS embeddings (
        --    embedding_id VARCHAR(50) PRIMARY KEY,
        --    source_type TEXT,                         -- نوع داده (چک لیست یا داده اصلی و...)
        --    ref_id TEXT,
        --    embedding_vector vector(768),             -- خلاصه اطلاعات در قالب بردار 
        --    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        --);
        """
        cur.execute(create_table_query)
        conn.commit()
        print("✅  ساخته شد (یا از قبل وجود داشت).")
        
        cur.close()
        conn.close()
        
    except OperationalError as e:
        print(f"❌ خطا در اتصال به دیتابیس  یا ساخت جدول: {e}")

if __name__ == "__main__":
    create_table()