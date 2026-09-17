"""لایه‌ی پایگاه‌داده‌ی داشبورد (SQLite + SQLAlchemy) و مدل‌های ORM.

این پکیج عمداً هیچ چیزی را در ``__init__`` ایمپورت نمی‌کند تا ساخت engine
«تنبل» (lazy) بماند و ترتیب ایمپورت‌ها را پیچیده نکند:
``from api.db.base import SessionLocal, init_db`` و
``from api.db.models import Project, ...``.
"""
