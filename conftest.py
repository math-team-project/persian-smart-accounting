"""
پیکربندی سراسری pytest برای کل پروژه.

پروژه هیچ ``pyproject.toml``/``pytest.ini`` ندارد و ``tests/`` هم به‌عمد
``__init__.py`` ندارد (import مستقل هر فایل تست)، به همین دلیل pytest به‌طور
پیش‌فرض ریشه‌ی پروژه را به ``sys.path`` اضافه نمی‌کند. بدون این، `import api`
یا `import pipeline` در فایل‌های تست شکست می‌خورد. این فایل تضمین می‌کند که
صرف‌نظر از نحوه‌ی اجرای pytest (از ریشه‌ی پروژه یا هر زیرمسیر دیگر)، ریشه‌ی
پروژه در ``sys.path`` باشد -- دقیقاً همان کاری که ``pipeline.py`` خودش برای
``extraction_script`` انجام می‌دهد (نگاه کنید به هدر آن فایل).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
