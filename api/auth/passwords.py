"""هش و بررسی رمز عبور با ``passlib``.

طرح انتخابی ``pbkdf2_sha256`` است -- خالص-پایتون (هیچ کتابخانه‌ی باینری/کامپایل
لازم نیست، که روی ویندوز برای یک MVP دانشجویی مهم است) و همان الگوریتم توصیه‌شده‌ی
passlib برای این حالت. اگر روزی bcrypt/argon2 لازم شد، فقط همین فایل عوض می‌شود:
بقیه‌ی کد فقط ``hash_password`` و ``verify_password`` را می‌شناسد.
"""
from __future__ import annotations

from passlib.context import CryptContext

# ``deprecated="auto"`` یعنی هش‌های قوی‌ترِ اضافه‌شده در آینده به‌صورت خودکار
# هنگام اولین ورود موفق هر کاربر بازتولید می‌شوند (``needs_update``).
_pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

MIN_PASSWORD_LENGTH = 6


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _pwd_context.verify(password, password_hash)
    except ValueError:
        # هش ناشناخته/خراب در پایگاه‌داده -- ورود صرفاً ناموفق می‌شود.
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _pwd_context.needs_update(password_hash)
    except ValueError:  # pragma: no cover - هش نامعتبر
        return True
