"""
کتابخانه‌ی آیکون‌های وکتور (SVG) به سبک Lucide، برای رابط کاربری جدید (Jinja2).

این ماژول معادل و جایگزین ``legacy_streamlit/icons.py`` است -- همان ایده (SVG
درون‌خطی با ``stroke="currentColor"`` تا رنگ از طریق CSS محیط قابل کنترل
باشد) اما به‌عنوان یک تابع全局 Jinja2 به‌جای فراخوانی مستقیم پایتون در f-string
های Streamlit در دسترس قرار می‌گیرد (نگاه کنید به ``register_template_globals``
در ``api/main.py``).

مقادیر «icon» در ``pipeline.FILE_SLOTS`` باید با کلیدهای این دیکشنری مطابقت
داشته باشند.
"""
from __future__ import annotations

_ICON_PATHS: dict[str, str] = {
    "shield-check": (
        '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/>'
        '<path d="m9 12 2 2 4-4"/>'
    ),
    "file-spreadsheet": (
        '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
        '<path d="M14 2v6h6"/>'
        '<rect x="8" y="12.5" width="8" height="7"/>'
        '<line x1="8" y1="16" x2="16" y2="16"/>'
        '<line x1="12" y1="12.5" x2="12" y2="19.5"/>'
    ),
    "file-text": (
        '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
        '<path d="M14 2v6h6"/>'
        '<line x1="8" y1="13" x2="16" y2="13"/>'
        '<line x1="8" y1="17" x2="16" y2="17"/>'
    ),
    "upload-cloud": (
        '<path d="M4 14.9A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 .5 8.97"/>'
        '<path d="M12 12v9"/>'
        '<path d="m16 16-4-4-4 4"/>'
    ),
    "check-circle": ('<circle cx="12" cy="12" r="10"/>' '<path d="m9 12 2 2 4-4"/>'),
    "x-circle": (
        '<circle cx="12" cy="12" r="10"/>'
        '<line x1="15" y1="9" x2="9" y2="15"/>'
        '<line x1="9" y1="9" x2="15" y2="15"/>'
    ),
    "alert-triangle": (
        '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 '
        '1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/>'
        '<line x1="12" y1="9" x2="12" y2="13"/>'
        '<line x1="12" y1="17" x2="12.01" y2="17"/>'
    ),
    "eye": (
        '<path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7Z"/>'
        '<circle cx="12" cy="12" r="3"/>'
    ),
    "circle": '<circle cx="12" cy="12" r="9"/>',
    "asterisk": (
        '<line x1="12" y1="4" x2="12" y2="20"/>'
        '<line x1="5.5" y1="7.5" x2="18.5" y2="16.5"/>'
        '<line x1="18.5" y1="7.5" x2="5.5" y2="16.5"/>'
    ),
    "list-checks": (
        '<path d="m3 7 2 2 4-4"/>'
        '<path d="m3 15 2 2 4-4"/>'
        '<line x1="12" y1="8" x2="21" y2="8"/>'
        '<line x1="12" y1="16" x2="21" y2="16"/>'
    ),
    "database": (
        '<ellipse cx="12" cy="5" rx="8" ry="3"/>'
        '<path d="M4 5v14c0 1.66 3.58 3 8 3s8-1.34 8-3V5"/>'
        '<path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3"/>'
    ),
    "file-down": (
        '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
        '<path d="M14 2v6h6"/>'
        '<path d="M12 12v6"/>'
        '<path d="m9.5 15.5 2.5 2.5 2.5-2.5"/>'
    ),
    "bolt": '<path d="M13 2 3 14h7l-1 8 10-12h-7l1-8Z"/>',
    "download": (
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
        '<polyline points="7 10 12 15 17 10"/>'
        '<line x1="12" y1="15" x2="12" y2="3"/>'
    ),
    "search": ('<circle cx="11" cy="11" r="7"/>' '<line x1="21" y1="21" x2="16.65" y2="16.65"/>'),
    "sparkles": (
        '<path d="M12 3v4"/><path d="M12 17v4"/><path d="M3 12h4"/><path d="M17 12h4"/>'
        '<path d="m5.6 5.6 2.8 2.8"/><path d="m15.6 15.6 2.8 2.8"/>'
        '<path d="m5.6 18.4 2.8-2.8"/><path d="m15.6 8.4 2.8-2.8"/>'
    ),
    "chevron-right": '<polyline points="9 18 15 12 9 6"/>',
    "chevron-left": '<polyline points="15 18 9 12 15 6"/>',
    "info": (
        '<circle cx="12" cy="12" r="10"/>'
        '<line x1="12" y1="16" x2="12" y2="11.5"/>'
        '<line x1="12" y1="8" x2="12.01" y2="8"/>'
    ),
    "clock": ('<circle cx="12" cy="12" r="10"/>' '<polyline points="12 6 12 12 16 14"/>'),
    "arrow-left": ('<line x1="19" y1="12" x2="5" y2="12"/>' '<polyline points="12 19 5 12 12 5"/>'),
    "layers": (
        '<polygon points="12 2 2 7 12 12 22 7 12 2"/>'
        '<polyline points="2 17 12 22 22 17"/>'
        '<polyline points="2 12 12 17 22 12"/>'
    ),
}


def icon(name: str, size: int = 18, stroke_width: float = 1.8, css_class: str = "") -> str:
    """رشته‌ی HTML یک آیکون SVG درون‌خطی را برمی‌گرداند. برای استفاده در Jinja2
    به‌عنوان یک global ثبت می‌شود (نگاه کنید ``api/main.py``) و مقدار بازگشتی
    باید با فیلتر ``| safe`` رندر شود."""
    inner = _ICON_PATHS.get(name, _ICON_PATHS["circle"])
    classes = f"psa-icon {css_class}".strip()
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="{stroke_width}" '
        f'stroke-linecap="round" stroke-linejoin="round" class="{classes}" '
        f'style="vertical-align:-3px; flex-shrink:0;">{inner}</svg>'
    )
