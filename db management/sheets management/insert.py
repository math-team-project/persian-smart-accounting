import psycopg2
from psycopg2 import extras
import json

# اطلاعات اتصال به دیتابیس جدیدی که ساختیم
DB_NAME = "smart_acounting_db"
DB_USER = "postgres"
DB_PASSWORD = "90908080"      # پسورد  
DB_HOST = "localhost"
DB_PORT = "5432"

# def insert_sample_data():
#     conn = None
#     try:
#         conn = psycopg2.connect(
#             dbname=DB_NAME,
#             user=DB_USER,
#             password=DB_PASSWORD,
#             host=DB_HOST,
#             port=DB_PORT
#         )
#         conn.set_client_encoding('UTF8')  # تضمین ارتباط UTF-8 بین پایتون و دیتابیس
#         cur = conn.cursor()

    