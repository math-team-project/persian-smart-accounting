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
    "chevron-down": '<polyline points="6 9 12 15 18 9"/>',
    # آیکون‌های پوسته (روشن/تیره) -- در دکمهٔ تغییر پوستهٔ سربرگ استفاده می‌شوند
    "sun": (
        '<circle cx="12" cy="12" r="4"/>'
        '<path d="M12 2v2"/><path d="M12 20v2"/>'
        '<path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/>'
        '<path d="M2 12h2"/><path d="M20 12h2"/>'
        '<path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>'
    ),
    "moon": '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
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
    # ------------------------------------------------------------------
    # آیکون‌های افزوده‌شده در بازطراحی بصری رابط کاربری (۱۴۰۴) -- همه از
    # سبک خطی Lucide با ضخامت خط یکسان تا زبان بصری آیکون‌ها یکدست بماند.
    # ------------------------------------------------------------------
    "menu": (
        '<line x1="4" y1="7" x2="20" y2="7"/>'
        '<line x1="4" y1="12" x2="20" y2="12"/>'
        '<line x1="4" y1="17" x2="20" y2="17"/>'
    ),
    "x": ('<line x1="18" y1="6" x2="6" y2="18"/>' '<line x1="6" y1="6" x2="18" y2="18"/>'),
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "lock": (
        '<rect x="4" y="10.5" width="16" height="10.5" rx="2"/>'
        '<path d="M8 10.5V7a4 4 0 0 1 8 0v3.5"/>'
    ),
    "file-check": (
        '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
        '<path d="M14 2v6h6"/>'
        '<path d="m9 15.5 2 2 4-4.5"/>'
    ),
    "file-search": (
        '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
        '<path d="M14 2v6h6"/>'
        '<circle cx="11.5" cy="15" r="2.5"/>'
        '<line x1="13.5" y1="17" x2="16" y2="19.5"/>'
    ),
    "clipboard-check": (
        '<rect x="5" y="4" width="14" height="17" rx="2"/>'
        '<path d="M9 4V2.8h6V4"/>'
        '<path d="m8.5 12.5 2.2 2.2 4.3-4.7"/>'
    ),
    "cpu": (
        '<rect x="6" y="6" width="12" height="12" rx="2"/>'
        '<path d="M9.5 2.5v3.5M14.5 2.5v3.5M9.5 18v3.5M14.5 18v3.5"/>'
        '<path d="M2.5 9.5H6M2.5 14.5H6M18 9.5h3.5M18 14.5h3.5"/>'
    ),
    "gauge": (
        '<path d="M3.5 18a9 9 0 1 1 17 0"/>'
        '<path d="m12 13.5 4-4"/>'
        '<circle cx="12" cy="15" r="1.6"/>'
    ),
    "scan-text": (
        '<path d="M3 8V5.5A2.5 2.5 0 0 1 5.5 3H8"/>'
        '<path d="M16 3h2.5A2.5 2.5 0 0 1 21 5.5V8"/>'
        '<path d="M21 16v2.5A2.5 2.5 0 0 1 18.5 21H16"/>'
        '<path d="M8 21H5.5A2.5 2.5 0 0 1 3 18.5V16"/>'
        '<line x1="7.5" y1="9" x2="16.5" y2="9"/>'
        '<line x1="7.5" y1="12.5" x2="16.5" y2="12.5"/>'
        '<line x1="7.5" y1="16" x2="12.5" y2="16"/>'
    ),
    "workflow": (
        '<rect x="3" y="3" width="7" height="6" rx="1.6"/>'
        '<rect x="14" y="15" width="7" height="6" rx="1.6"/>'
        '<path d="M6.5 9v5.5a2.5 2.5 0 0 0 2.5 2.5h5"/>'
        '<path d="M17.5 15V9.5A2.5 2.5 0 0 0 15 7h-4.5"/>'
    ),
    "trending-up": (
        '<polyline points="3 17 9.5 10.5 13.5 14.5 21 7"/>'
        '<polyline points="15.5 7 21 7 21 12.5"/>'
    ),
    "wallet": (
        '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H18a2 2 0 0 1 2 2v1"/>'
        '<rect x="3" y="7.5" width="18" height="11.5" rx="2.5"/>'
        '<path d="M16.5 13.2h.01"/>'
    ),
    "users": (
        '<circle cx="9.5" cy="8" r="3.5"/>'
        '<path d="M3 20a6.5 6.5 0 0 1 13 0"/>'
        '<path d="M16.5 5.2a3.5 3.5 0 0 1 0 6.6"/>'
        '<path d="M18 14.5A6 6 0 0 1 21 20"/>'
    ),
    "filter": '<polygon points="21 4 3 4 10 12.5 10 20 14 18 14 12.5 21 4"/>',
    "refresh-cw": (
        '<path d="M20.5 11a8.5 8.5 0 0 0-14.6-5.4L3.5 7.5"/>'
        '<path d="M3.5 13a8.5 8.5 0 0 0 14.6 5.4l2.4-1.9"/>'
        '<polyline points="3.5 3.5 3.5 7.5 7.5 7.5"/>'
        '<polyline points="20.5 20.5 20.5 16.5 16.5 16.5"/>'
    ),
    "alert-circle": (
        '<circle cx="12" cy="12" r="9"/>'
        '<line x1="12" y1="7.5" x2="12" y2="13"/>'
        '<line x1="12" y1="16.5" x2="12.01" y2="16.5"/>'
    ),
    "landmark": (
        '<line x1="3" y1="21" x2="21" y2="21"/>'
        '<line x1="5.5" y1="21" x2="5.5" y2="11"/>'
        '<line x1="10" y1="21" x2="10" y2="11"/>'
        '<line x1="14" y1="21" x2="14" y2="11"/>'
        '<line x1="18.5" y1="21" x2="18.5" y2="11"/>'
        '<polygon points="12 2 21 7.5 3 7.5"/>'
    ),
    "minus": '<line x1="5" y1="12" x2="19" y2="12"/>',
    # آیکون‌های بخش «تنظیمات هوش مصنوعی» (فاز تنظیمات).
    "settings": (
        '<line x1="4" y1="7" x2="20" y2="7"/>'
        '<line x1="4" y1="12" x2="20" y2="12"/>'
        '<line x1="4" y1="17" x2="20" y2="17"/>'
        '<circle cx="9" cy="7" r="2.4"/>'
        '<circle cx="15" cy="12" r="2.4"/>'
        '<circle cx="8" cy="17" r="2.4"/>'
    ),
    "key": (
        '<circle cx="8" cy="15" r="4"/>'
        '<path d="m11 12 8-8"/>'
        '<path d="m17 6 2 2"/>'
        '<path d="m19 4 2 2"/>'
    ),
    "plug": (
        '<path d="M9 3v6"/>'
        '<path d="M15 3v6"/>'
        '<path d="M6 9h12v3a6 6 0 0 1-12 0V9Z"/>'
        '<path d="M12 18v3"/>'
    ),
    # آیکون‌های کارگاه «چت‌بات مالی»: ساخت گفتگوی جدید، نشان هر گفتگو، و حذف.
    "plus": ('<line x1="12" y1="5" x2="12" y2="19"/>' '<line x1="5" y1="12" x2="19" y2="12"/>'),
    "message-square": (
        '<path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z"/>'
    ),
    "trash": (
        '<path d="M3 6h18"/>'
        '<path d="M8 6V4h8v2"/>'
        '<path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>'
        '<line x1="10" y1="11" x2="10" y2="17"/>'
        '<line x1="14" y1="11" x2="14" y2="17"/>'
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
