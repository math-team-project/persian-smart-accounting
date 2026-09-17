"""ابزار خط فرمان داشبورد: ساخت کاربر (و کارهای راهبری ساده).

اجرا از ریشه‌ی پروژه::

    python -m api.cli create-user --username admin
    python -m api.cli create-user --username admin --password "..."   # بدون پرسش تعاملی
    python -m api.cli list-users

نخستین کاربر را می‌توان از این مسیر یا از صفحهٔ ``/setup`` (که فقط تا وقتی هیچ
کاربری وجود ندارد فعال است) ساخت. رمز عبور هرگز در تاریخچه‌ی شل ذخیره نمی‌شود
مگر با پرچم صریح ``--password`` که برای اسکریپت‌های خودکار است.
"""
from __future__ import annotations

import argparse
import getpass
import sys

from api.auth import passwords
from api.db.base import SessionLocal, init_db
from api.repositories import users as users_repo


def _prompt_password() -> str | None:
    password = getpass.getpass("رمز عبور: ")
    if len(password) < passwords.MIN_PASSWORD_LENGTH:
        print(f"خطا: رمز عبور باید حداقل {passwords.MIN_PASSWORD_LENGTH} نویسه باشد.", file=sys.stderr)
        return None
    if password != getpass.getpass("تکرار رمز عبور: "):
        print("خطا: تکرار رمز عبور مطابقت ندارد.", file=sys.stderr)
        return None
    return password


def create_user(username: str, password: str | None) -> int:
    username = username.strip()
    if not username:
        print("خطا: نام کاربری الزامی است.", file=sys.stderr)
        return 2

    if password is None:
        password = _prompt_password()
        if password is None:
            return 2

    init_db()
    with SessionLocal() as session:
        if users_repo.get_by_username(session, username) is not None:
            print(f"خطا: کاربر «{username}» از قبل وجود دارد.", file=sys.stderr)
            return 1
        users_repo.create(session, username, passwords.hash_password(password))
        total = users_repo.count_users(session)

    print(f"کاربر «{username}» ساخته شد (تعداد کاربران: {total}).")
    return 0


def list_users() -> int:
    init_db()
    with SessionLocal() as session:
        from sqlalchemy import select

        from api.db.models import User

        users = list(session.scalars(select(User).order_by(User.id)))
    if not users:
        print("هیچ کاربری ساخته نشده است. برای ساخت نخستین کاربر: python -m api.cli create-user")
        return 0
    for user in users:
        print(f"{user.id}\t{user.username}\t{user.created_at:%Y-%m-%d %H:%M}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="api.cli", description="ابزار راهبری داشبورد حسابرسی")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create-user", help="ساخت یک کاربر جدید")
    create_parser.add_argument("--username", "-u", required=True, help="نام کاربری")
    create_parser.add_argument(
        "--password",
        "-p",
        default=None,
        help="رمز عبور (اگر داده نشود، به‌صورت امن پرسیده می‌شود)",
    )

    subparsers.add_parser("list-users", help="نمایش کاربران موجود")

    args = parser.parse_args(argv)
    if args.command == "create-user":
        return create_user(args.username, args.password)
    if args.command == "list-users":
        return list_users()
    parser.print_help()
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
