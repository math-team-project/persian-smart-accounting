import psycopg2
from psycopg2 import extras
import json

# اطلاعات اتصال به دیتابیس جدیدی که ساختیم
DB_NAME = "json_sql_db"
DB_USER = "postgres"
DB_PASSWORD = "90908080"      # ⚠️ پسورد خودت رو بگذار
DB_HOST = "localhost"
DB_PORT = "5432"

def insert_sample_data():
    conn = None
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT
        )
        cur = conn.cursor()

        # چند نمونه داده با ساختار JSON متفاوت
        products = [
            ("لپ‌تاپ", {
                "brand": "Apple",
                "model": "MacBook Pro",
                "specs": {
                    "ram": "16GB",
                    "storage": "512GB SSD",
                    "cpu": "M3"
                },
                "in_stock": True,
                "price_usd": 1999
            }),
            ("هدفون", {
                "brand": "Sony",
                "model": "WH-1000XM5",
                "color": "black",
                "wireless": True,
                "noise_canceling": True,
                "price_usd": 399
            }),
            ("کتاب", {
                "title": "Clean Code",
                "author": "Robert C. Martin",
                "pages": 464,
                "publisher": "Prentice Hall",
                "tags": ["programming", "software", "design"]
            })
        ]

        # روش اول: درج تک‌تک با استفاده از تبدیل خودکار دیکشنری به JSONB
        for name, attrs in products:
            # psycopg2 به‌طور خودکار دیکشنری پایتون را به JSONB تبدیل می‌کند
            insert_query = """
            INSERT INTO products (name, attributes)
            VALUES (%s, %s)
            """
            cur.execute(insert_query, (name, json.dumps(attrs)))
            print(f"✅ محصول '{name}' درج شد.")

        # روش دوم: درج چندتایی با استفاده از execute_values (بهینه‌تر)
        # برای نشان دادن تنوع، دو محصول دیگر با استفاده از این روش اضافه می‌کنیم
        more_products = [
            ("مانیتور", {"brand": "Dell", "size": "27inch", "resolution": "4K", "price_usd": 599}),
            ("ماوس", {"brand": "Logitech", "type": "wireless", "dpi": 3200, "color": "gray"})
        ]
        
        # استفاده از extras.execute_values برای درج چندتایی با سرعت بالا
        insert_many_query = """
        INSERT INTO products (name, attributes) VALUES %s
        """
        # باید داده‌ها را به شکل لیستی از تاپل‌ها درآوریم
        data_list = [(name, json.dumps(attrs)) for name, attrs in more_products]
        extras.execute_values(cur, insert_many_query, data_list)
        print(f"✅ {len(more_products)} محصول دیگر با روش بهینه درج شد.")

        conn.commit()
        print("🎉 همه داده‌ها با موفقیت ذخیره شدند.")

        # --- نمایش داده‌های درج‌شده برای تأیید ---
        cur.execute("SELECT id, name, attributes FROM products ORDER BY id;")
        rows = cur.fetchall()
        print("\n📋 محتویات جدول بعد از درج:")
        for row in rows:
            print(f"ID: {row[0]}, Name: {row[1]}, Attributes: {json.dumps(row[2], ensure_ascii=False, indent=2)}")
        print("---")

        cur.close()
        conn.close()

    except psycopg2.Error as e:
        print(f"❌ خطای دیتابیس: {e}")
        if conn:
            conn.rollback()

if __name__ == "__main__":
    insert_sample_data()