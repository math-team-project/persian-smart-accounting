"""
آداپتور کوچک بین فایل‌های آپلودی FastAPI و توابع موجود ``pipeline.py``.

``pipeline.save_uploaded_file`` و ``pipeline.save_report_upload`` اصلاً تغییر
نکرده‌اند -- طبق الزامات فاز ۱، منطق پردازش فایل باید بدون تغییر از پایپلاین
فراخوانی شود. آن دو تابع در اصل برای شیء ``UploadedFile`` استریم‌لیت نوشته
شده‌اند که فقط دو چیز از آن استفاده می‌شود: ویژگی ``.name`` و متد
``.getbuffer()``. به‌جای تغییر امضای pipeline.py، همین دو ویژگی را روی یک
شیء ساده که از بایت‌های خام (که در لایه‌ی API از ``UploadFile.read()`` گرفته
شده) تقلید می‌کنیم. این کوچک‌ترین تغییر ممکن است چون هیچ خطی از pipeline.py
دستکاری نمی‌شود.
"""
from __future__ import annotations


class InMemoryUploadAdapter:
    """اشیاء این کلاس دقیقا رابط مورد استفاده‌ی pipeline.py را دارند:
    ``.name`` (نام فایل) و ``.getbuffer()`` (محتوای خام، به‌صورت bytes)."""

    __slots__ = ("name", "_content")

    def __init__(self, filename: str, content: bytes) -> None:
        self.name = filename
        self._content = content

    def getbuffer(self) -> bytes:
        return self._content
