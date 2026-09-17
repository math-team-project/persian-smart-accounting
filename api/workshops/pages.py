"""کمک‌تابع‌های مشترک برای رندر صفحه‌ی کارگاه‌ها.

قالب پایه (``base.html``) به ``user`` و ``project`` نیاز دارد تا ناوبری را
داینامیک بسازد، و هر صفحه‌ی کارگاه به ``workshop``/``api_base`` نیاز دارد تا
آدرس‌های خود را از رجیستری بگیرد (نه هاردکد). این تابع تنها جایی است که این
بافتار ساخته می‌شود تا هیچ قالبی مجبور نباشد مسیر یا نام کارگاهی را دستی بداند.
"""
from __future__ import annotations

from typing import Any

from api.db.models import Project, User
from api.workshops.registry import WorkshopDefinition, all_workshops


def workshop_page_context(
    project: Project,
    user: User,
    workshop: WorkshopDefinition,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "user": user,
        "project": project,
        "workshop": workshop,
        "workshops": all_workshops(),
        "api_base": workshop.api_url(project.id),
        **extra,
    }


def project_page_context(project: Project, user: User, **extra: Any) -> dict[str, Any]:
    return {
        "user": user,
        "project": project,
        "workshops": all_workshops(),
        **extra,
    }
