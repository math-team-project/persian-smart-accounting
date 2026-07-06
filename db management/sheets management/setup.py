import psycopg2
from psycopg2 import sql, OperationalError

# اطلاعات اتصال به دیتابیس پیش‌فرض 'postgres'
DB_NAME = "postgres"        # دیتابیس سیستمی پیش‌فرض
DB_USER = "postgres"        # کاربر پیش‌فرض
DB_PASSWORD = "90908080"      # پسورد
DB_HOST = "localhost"
DB_PORT = "5432"

NEW_DB_NAME = "smart_acounting_db"  # نام دیتابیسی که می‌خواهیم بسازیم

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
            cur.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'fa_IR.UTF-8' LC_CTYPE 'fa_IR.UTF-8'").format(sql.Identifier(NEW_DB_NAME)))
            print(f"✅ دیتابیس '{NEW_DB_NAME}' با موفقیت ساخته شد.")
        else:
            print(f"ℹ️ دیتابیس '{NEW_DB_NAME}' قبلاً وجود داشت.")
            
        cur.close()
        conn.close()
        
    except OperationalError as e:
        print(f"❌ خطا در اتصال یا ساخت دیتابیس: {e}")


if __name__ == "__main__":
    create_database()