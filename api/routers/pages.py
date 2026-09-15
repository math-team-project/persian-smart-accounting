"""صفحات HTML سرور-رندر (Jinja2). این روتر هیچ منطق پردازشی ندارد -- فقط
قالب مناسب را با داده‌های لازم (مثلاً تعریف اسلات‌های آپلود) رندر می‌کند."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from api.services.checklist_service import get_file_slots
from api.templating import templates

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
async def landing_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {})


@router.get("/checklist", response_class=HTMLResponse)
async def checklist_page(request: Request) -> HTMLResponse:
    file_slots = get_file_slots()
    required_slots = {k: v for k, v in file_slots.items() if v["required"]}
    optional_slots = {k: v for k, v in file_slots.items() if not v["required"]}
    return templates.TemplateResponse(
        request,
        "checklist.html",
        {
            "required_slots": required_slots,
            "optional_slots": optional_slots,
        },
    )


@router.get("/audit-summary", response_class=HTMLResponse)
async def audit_summary_page(request: Request) -> HTMLResponse:
    # اسلات آپلود تکی برای گزارش حسابرسی -- با همان قالب دیکشنری که کامپوننت
    # مشترک dropzone.html انتظار دارد (label/help/required/icon/types).
    #
    # تبصره: برخلاف چک‌لیست (که ``pipeline.FILE_SLOTS`` را به‌عنوان منبع واحد دارد
    # و از طریق ``get_file_slots()`` مصرف می‌شود)، کارگاه خلاصه‌سازی تنها یک
    # اسلات آپلود دارد و ``audit_pipeline.py`` (خارج از محدوده‌ی این فاز) معادل
    # ``FILE_SLOTS`` را تعریف نمی‌کند، به همین دلیل این دیکشنری اینجا هاردکد شده
    # است (تنها استثنای شناخته‌شده به قاعده‌ی «منبع واحد در pipeline.py»). اگر در آینده
    # کارگاه سومی اضافه شد که بیش از یک اسلات آپلود دارد، بهتر است این دیکشنری را به
    # یک تابع در همان ماژول (مشابه ``get_file_slots()``) منتقل کرد -- نگاه کنید README
    # (بخش افزودنی کارگاه سوم) برای جزئیات بیشتر.
    audit_report_slot = {
        "label": "گزارش حسابرسی",
        "help": "گزارش حسابرسی موجود برای خلاصه‌سازی با کمک هوش مصنوعی",
        "required": True,
        "icon": "file-text",
        "types": ["pdf", "doc", "docx"],
    }
    return templates.TemplateResponse(
        request,
        "audit_summary.html",
        {
            "slot_key": "audit_report",
            "slot": audit_report_slot,
        },
    )
