import psycopg2
import json

DB_NAME = "json_sql_db"
DB_USER = "postgres"
DB_PASSWORD = "90908080"      # ⚠️ پسورد خودت رو بگذار
DB_HOST = "localhost"
DB_PORT = "5432"

def run_queries():
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

        print("=" * 60)
        print("🔍 ۱. نمایش همه محصولات به همراه برند (استخراج فیلد خاص)")
        print("=" * 60)
        # عملگر ->> متن (رشته) را برمی‌گرداند
        cur.execute("""
            SELECT 
                name, 
                attributes->>'brand' AS brand,
                attributes->>'price_usd' AS price
            FROM products
            ORDER BY id;
        """)
        for row in cur.fetchall():
            print(f"محصول: {row[0]}, برند: {row[1]}, قیمت: {row[2]}")

        print("\n" + "=" * 60)
        print("🔍 ۲. فیلتر: فقط محصولاتی که برندشان 'Apple' است")
        print("=" * 60)
        # استفاده از @> برای بررسی وجود کلید-مقدار در JSON
        cur.execute("""
            SELECT name, attributes
            FROM products
            WHERE attributes @> '{"brand": "Apple"}';
        """)
        for row in cur.fetchall():
            print(f"✅ {row[0]}: {json.dumps(row[1], ensure_ascii=False)}")

        print("\n" + "=" * 60)
        print("🔍 ۳. فیلتر: محصولاتی که قیمت (price_usd) کمتر از ۵۰۰ دارند")
        print("=" * 60)
        # تبدیل مقدار JSON به عدد و مقایسه
        cur.execute("""
            SELECT name, attributes->>'price_usd' AS price
            FROM products
            WHERE (attributes->>'price_usd')::INTEGER < 500;
        """)
        for row in cur.fetchall():
            print(f"✅ {row[0]} - قیمت: {row[1]}")

        print("\n" + "=" * 60)
        print("🔍 ۴. جستجوی تو در تو: محصولاتی که رم (ram) آنها '16GB' است")
        print("=" * 60)
        # دسترسی به فیلد specs که خودش یک آبجکت JSON است
        cur.execute("""
            SELECT name, attributes->'specs'->>'ram' AS ram
            FROM products
            WHERE attributes->'specs'->>'ram' = '16GB';
        """)
        for row in cur.fetchall():
            print(f"✅ {row[0]} - رم: {row[1]}")

        print("\n" + "=" * 60)
        print("🔍 ۵. بررسی وجود کلید: محصولاتی که کلید 'wireless' دارند")
        print("=" * 60)
        # عملگر ? بررسی می‌کند که آیا کلید در سطح اول JSON وجود دارد
        cur.execute("""
            SELECT name, attributes
            FROM products
            WHERE attributes ? 'wireless';
        """)
        for row in cur.fetchall():
            print(f"✅ {row[0]}: دارای ویژگی wireless = {row[1].get('wireless')}")

        print("\n" + "=" * 60)
        print("🔍 ۶. مرتب‌سازی بر اساس فیلد JSON (قیمت)")
        print("=" * 60)
        cur.execute("""
            SELECT name, attributes->>'price_usd' AS price
            FROM products
            WHERE attributes ? 'price_usd'
            ORDER BY (attributes->>'price_usd')::INTEGER DESC;
        """)
        for row in cur.fetchall():
            print(f"✅ {row[0]} - قیمت: {row[1]}")

        cur.close()
        conn.close()

    except psycopg2.Error as e:
        print(f"❌ خطای دیتابیس: {e}")

if __name__ == "__main__":
    run_queries()