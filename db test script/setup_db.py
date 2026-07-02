import psycopg2
from psycopg2 import sql, OperationalError

# اطلاعات اتصال به دیتابیس پیش‌فرض 'postgres'
DB_NAME = "postgres"        # دیتابیس سیستمی پیش‌فرض
DB_USER = "postgres"        # کاربر پیش‌فرض
DB_PASSWORD = "90908080"      # ⚠️ اینجا پسورد خودت را بگذار
DB_HOST = "localhost"
DB_PORT = "5432"

NEW_DB_NAME = "json_sql_db"  # نام دیتابیسی که می‌خواهیم بسازیم

def create_database():
    """اتصال به دیتابیس پیش‌فرض و ساخت دیتابیس جدید"""
    conn = None
    try:
        # اتصال به دیتابیس سیستمی 'postgres'
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT
        )
        conn.autocommit = True  # برای اجرای دستور CREATE DATABASE نیاز است
        cur = conn.cursor()
        
        # بررسی می‌کنیم که دیتابیس جدید وجود نداشته باشد
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (NEW_DB_NAME,))
        exists = cur.fetchone()
        if not exists:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(NEW_DB_NAME)))
            print(f"✅ دیتابیس '{NEW_DB_NAME}' با موفقیت ساخته شد.")
        else:
            print(f"ℹ️ دیتابیس '{NEW_DB_NAME}' قبلاً وجود داشت.")
            
        cur.close()
        conn.close()
        
    except OperationalError as e:
        print(f"❌ خطا در اتصال یا ساخت دیتابیس: {e}")

def create_table():
    """اتصال به دیتابیس جدید و ساختن جدول با فیلد JSONB"""
    conn = None
    try:
        conn = psycopg2.connect(
            dbname=NEW_DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT
        )
        cur = conn.cursor()
        
        # دستور CREATE TABLE با استفاده از نوع JSONB
        create_table_query = """
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            attributes JSONB,          -- اینجا داده‌های JSON ذخیره می‌شود
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
        cur.execute(create_table_query)
        conn.commit()
        print("✅ جدول 'products' با ستون JSONB ساخته شد (یا از قبل وجود داشت).")
        
        cur.close()
        conn.close()
        
    except OperationalError as e:
        print(f"❌ خطا در اتصال به دیتابیس جدید یا ساخت جدول: {e}")

if __name__ == "__main__":
    create_database()
    create_table()