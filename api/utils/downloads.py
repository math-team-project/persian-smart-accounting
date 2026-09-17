"""ساخت پاسخ HTTP برای دانلود فایل نتیجه (Word).

پیش‌تر همین منطق (کدگذاری RFC 5987 برای نام فایل فارسی) در هر دو روتر کارگاه
تکرار شده بود؛ این‌جا یک‌جا و مشترک شده است.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi.responses import Response

from api.workshops.registry import DOCX_MIME


def docx_response(content: bytes, filename: str, *, ascii_fallback_stem: str = "result") -> Response:
    """پاسخ دانلود یک فایل Word با نام فارسی.

    هدر HTTP فقط latin-1 را می‌پذیرد، بنابراین نام فارسی با کدگذاری
    ``filename*=UTF-8''...`` (RFC 5987) فرستاده می‌شود و یک نام ساده‌ی ASCII هم
    به‌عنوان fallback برای مرورگرهای قدیمی‌تر کنارش می‌آید.
    """
    suffix = Path(filename).suffix or ".docx"
    encoded_filename = quote(filename)
    return Response(
        content=content,
        media_type=DOCX_MIME,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_fallback_stem}{suffix}"; '
                f"filename*=UTF-8''{encoded_filename}"
            ),
        },
    )
