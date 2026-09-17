"""پرس‌وجوهای مربوط به کاربران داشبورد."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.db.models import User


def count_users(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(User)) or 0)


def get_by_username(session: Session, username: str) -> Optional[User]:
    return session.scalar(select(User).where(User.username == username.strip()))


def get(session: Session, user_id: int) -> Optional[User]:
    return session.get(User, user_id)


def create(session: Session, username: str, password_hash: str) -> User:
    user = User(username=username.strip(), password_hash=password_hash)
    session.add(user)
    session.commit()
    return user


def update_password_hash(session: Session, user: User, password_hash: str) -> None:
    """به‌روزرسانی هش رمز (پس از تغییر طرح هش در passlib)."""
    user.password_hash = password_hash
    session.commit()
