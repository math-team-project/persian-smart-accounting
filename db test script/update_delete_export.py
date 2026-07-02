import psycopg2
import json

DB_NAME = "json_sql_db"
DB_USER = "postgres"
DB_PASSWORD = "90908080"      # ⚠️ پسورد خودت
DB_HOST = "localhost"
DB_PORT = "5432"

def update_delete_export():
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

        # ---------- ۱. به‌روزرسانی: اضافه کردن فیلد جدید به JSON ----------
        print("=" * 60)
        print("🔄 ۱. به‌روزرسانی: افزودن فیلد 'discount' به محصول 'لپ‌تاپ'")
        print("=" * 60)
        # استفاده از jsonb_set برای اضافه/تغییر یک فیلد در JSON
        cur.execute("""
            UPDATE products
            SET attributes = jsonb_set(
                attributes, 
                '{discount}', 
                '"10%"'   -- مقدار جدید به صورت رشته JSON
            )
            WHERE name = 'لپ‌تاپ'
            RETURNING id, name, attributes;
        """)
        updated = cur.fetchone()
        if updated:
            print(f"✅ به‌روزرسانی شد: {updated[1]} -> {json.dumps(updated[2], ensure_ascii=False)}")
        else:
            print("❌ محصول 'لپ‌تاپ' پیدا نشد.")

        # ---------- ۲. به‌روزرسانی: تغییر مقدار موجود در JSON ----------
        print("\n" + "=" * 60)
        print("🔄 ۲. به‌روزرسانی: تغییر برند 'Sony' به 'Sony Electronics'")
        print("=" * 60)
        cur.execute("""
            UPDATE products
            SET attributes = jsonb_set(
                attributes,
                '{brand}',
                '"Sony Electronics"'
            )
            WHERE attributes->>'brand' = 'Sony'
            RETURNING id, name, attributes;
        """)
        updated = cur.fetchone()
        if updated:
            print(f"✅ به‌روزرسانی شد: {updated[1]} -> برند جدید: {updated[2]['brand']}")
        else:
            print("❌ محصولی با برند 'Sony' پیدا نشد.")

        # ---------- ۳. حذف یک محصول بر اساس شرط JSON ----------
        print("\n" + "=" * 60)
        print("🗑️ ۳. حذف محصولی که کلید 'tags' دارد (کتاب Clean Code)")
        print("=" * 60)
        cur.execute("""
            DELETE FROM products
            WHERE attributes ? 'tags'
            RETURNING id, name;
        """)
        deleted = cur.fetchone()
        if deleted:
            print(f"✅ حذف شد: {deleted[1]} (ID: {deleted[0]})")
        else:
            print("❌ هیچ محصولی با کلید 'tags' یافت نشد.")

        # ---------- ۴. خروجی گرفتن از کل جدول به صورت فایل JSON ----------
        print("\n" + "=" * 60)
        print("📤 ۴. صادر کردن تمام داده‌های باقی‌مانده به فایل 'products_export.json'")
        print("=" * 60)
        cur.execute("""
            SELECT id, name, attributes, created_at
            FROM products
            ORDER BY id;
        """)
        rows = cur.fetchall()
        
        # تبدیل به لیست دیکشنری‌ها
        export_data = []
        for row in rows:
            export_data.append({
                "id": row[0],
                "name": row[1],
                "attributes": row[2],  # خود دیکشنری
                "created_at": row[3].isoformat() if row[3] else None
            })

        # نوشتن در فایل JSON با فرمت زیبا
        with open("products_export.json", "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)
        
        print(f"✅ {len(export_data)} رکورد در فایل 'products_export.json' ذخیره شد.")
        print("محتوای فایل:")
        print(json.dumps(export_data, ensure_ascii=False, indent=2))

        # ---------- ۵. نمایش نهایی جدول برای تأیید ----------
        print("\n" + "=" * 60)
        print("📋 محتویات نهایی جدول:")
        print("=" * 60)
        cur.execute("SELECT id, name, attributes FROM products ORDER BY id;")
        for row in cur.fetchall():
            print(f"ID: {row[0]}, Name: {row[1]}, Attributes: {json.dumps(row[2], ensure_ascii=False)}")

        conn.commit()
        cur.close()
        conn.close()

        print("\n🎉 همه عملیات با موفقیت انجام شد!")

    except psycopg2.Error as e:
        print(f"❌ خطای دیتابیس: {e}")
        if conn:
            conn.rollback()

if __name__ == "__main__":
    update_delete_export()