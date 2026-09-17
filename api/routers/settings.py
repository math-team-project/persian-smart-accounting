"""روتر تنظیمات هوش مصنوعی هر کارگاه در هر پروژه.

سه endpoint:

* ``GET  /api/projects/{project_id}/settings``                    -- وضعیت فعلی
* ``PUT  /api/projects/{project_id}/settings/{workshop_slug}``     -- ذخیره
* ``POST /api/projects/{project_id}/settings/{workshop_slug}/test`` -- تست اتصال

هیچ‌کدام از این پاسخ‌ها کلید API را برنمی‌گرداند؛ فقط ``api_key_set`` که می‌گوید
مقداری ذخیره شده است. فهرست کارگاه‌های مجاز هم از خود رجیستری خوانده می‌شود
(``workshops_with_settings``) نه از یک لیست هاردکد در این فایل.

 endpoint ها عمداً ``def`` (نه ``async def``) هستند تا فراخوانی مسدودکننده‌ی
شبکه در «تست اتصال» نوبت‌دهی موتور async را قفل نکند؛ FastAPI چنین توابعی را در
یک ترد کارگر اجرا می‌کند.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth.deps import require_api_project
from api.db.base import get_session
from api.db.models import Project
from api.schemas.settings import (
    AIDefaultsOut,
    ConnectionTestOut,
    ProjectAISettingsOut,
    WorkshopAISettingsInput,
    WorkshopAISettingsOut,
)
from api.services import ai_settings as ai_settings_service
from api.services.ai_settings import AISettingsError
from api.workshops.registry import (
    WorkshopDefinition,
    get as get_workshop,
    workshops_with_settings,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/projects/{project_id}/settings",
    tags=["settings"],
)

NOT_CONFIGURABLE = "برای این کارگاه تنظیمات اختصاصی هوش مصنوعی وجود ندارد."


def _configurable_workshop(workshop_slug: str) -> WorkshopDefinition:
    """کارگاه درخواستی، فقط اگر تنظیماتش واقعاً در اجرا اعمال شود."""
    workshop = get_workshop(workshop_slug)
    if workshop is None or not workshop.settings_applied:
        raise HTTPException(status_code=404, detail=NOT_CONFIGURABLE)
    return workshop


def _workshop_out(panel: dict[str, Any], workshop: WorkshopDefinition) -> WorkshopAISettingsOut:
    return WorkshopAISettingsOut(
        slug=workshop.slug,
        display_name_fa=workshop.display_name_fa,
        api_key_set=panel["api_key_set"],
        api_url=panel["api_url"] or None,
        model=panel["model"] or None,
        temperature=panel["temperature"],
        max_output_tokens=panel["max_output_tokens"],
    )


def _defaults_out(settings: Any) -> AIDefaultsOut:
    return AIDefaultsOut(
        api_url=settings.api_url,
        model=settings.model,
        temperature=settings.temperature,
        max_output_tokens=settings.max_output_tokens,
        api_key_set=settings.has_api_key,
    )


@router.get("", response_model=ProjectAISettingsOut)
def get_project_ai_settings(
    project: Project = Depends(require_api_project),
    session: Session = Depends(get_session),
) -> ProjectAISettingsOut:
    panels = [
        ai_settings_service.panel_for(session, project.id, workshop)
        for workshop in workshops_with_settings()
    ]
    return ProjectAISettingsOut(
        project_id=project.id,
        defaults=_defaults_out(ai_settings_service.default_ai_settings()),
        workshops=[_workshop_out(panel, panel["workshop"]) for panel in panels],
    )


@router.put("/{workshop_slug}", response_model=WorkshopAISettingsOut)
def update_workshop_ai_settings(
    workshop_slug: str,
    payload: WorkshopAISettingsInput,
    project: Project = Depends(require_api_project),
    session: Session = Depends(get_session),
) -> WorkshopAISettingsOut:
    workshop = _configurable_workshop(workshop_slug)
    try:
        ai_settings_service.save_ai_settings(
            session,
            project.id,
            workshop.slug,
            api_key=payload.api_key,
            clear_api_key=payload.clear_api_key,
            api_url=payload.api_url,
            model=payload.model,
            temperature=payload.temperature,
            max_output_tokens=payload.max_output_tokens,
        )
    except AISettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    panel = ai_settings_service.panel_for(session, project.id, workshop)
    return _workshop_out(panel, workshop)


@router.post("/{workshop_slug}/test", response_model=ConnectionTestOut)
def test_workshop_connection(
    workshop_slug: str,
    payload: WorkshopAISettingsInput,
    project: Project = Depends(require_api_project),
    session: Session = Depends(get_session),
) -> ConnectionTestOut:
    """یک درخواست کمینه به سرویس می‌فرستد تا کاربر پیش از اجرای کامل مطمئن شود.

    مقادیر فرم (که هنوز ذخیره نشده‌اند) روی تنظیمات مؤثر فعلی سوار می‌شوند،
    بنابراین کاربر می‌تواند کلید/آدرس/مدل جدید را قبل از ذخیره آزمایش کند.
    کلید خالی یعنی «همان کلید ذخیره‌شده/پیش‌فرض».
    """
    workshop = _configurable_workshop(workshop_slug)
    if workshop.test_connection is None:
        raise HTTPException(status_code=400, detail="این کارگاه تست اتصال ندارد.")

    # مقادیر عددی فرم با همان قواعد «ذخیره» اعتبارسنجی می‌شوند: ورودی نامعتبر
    # باید پیش از هر تماس با مدل و با همان پیام فارسی رد شود.
    try:
        ai_settings_service.validate_overrides(
            temperature=payload.temperature, max_output_tokens=payload.max_output_tokens
        )
    except AISettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    current = ai_settings_service.resolve_ai_settings(session, project.id, workshop.slug)
    probe = ai_settings_service.overlay(
        current,
        api_key=_clean(payload.api_key),
        api_url=_clean(payload.api_url),
        model=_clean(payload.model),
        temperature=payload.temperature,
        max_output_tokens=payload.max_output_tokens,
    )

    try:
        message = workshop.test_connection(probe)
    except AISettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 -- هر شکست اتصال باید پیام قابل‌نمایش بدهد
        logger.info(
            "connection test failed (project=%s, workshop=%s): %s",
            project.id,
            workshop.slug,
            exc,
        )
        return ConnectionTestOut(ok=False, message=_failure_message(exc))

    return ConnectionTestOut(ok=True, message=message)


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _failure_message(exc: Exception) -> str:
    """پیام فارسی یکدست برای شکست تست اتصال (متن اصلی خطا حفظ می‌شود)."""
    detail = str(exc).strip()
    return f"اتصال برقرار نشد: {detail}" if detail else "اتصال برقرار نشد."
