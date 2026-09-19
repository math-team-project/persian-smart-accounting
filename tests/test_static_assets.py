"""تست‌های سرو فایل‌های استاتیک (``/static/...``).

این پروژه هیچ مرحله‌ی build و هیچ هش محتوایی در نام فایل‌ها ندارد. بدون هدر
``Cache-Control: no-cache`` مرورگر می‌تواند تا مدت نامعلومی نسخه‌ی قدیمی
``.js``/``.css`` را اجرا کند و کاربر پس از هر به‌روزرسانی رفتار قدیمی را ببیند.
این تست همان تضمین را قفل می‌کند: مرورگر همیشه اعتبارسنجی می‌کند (و چون ``ETag``
موجود است، پاسخ معمول ``304`` است).
"""
from __future__ import annotations

from api.main import WEB_DIR

STATIC_ASSETS = [
    "/static/css/app.css",
    "/static/js/checklist.js",
    "/static/js/audit_summary.js",
    "/static/js/budget_analysis.js",
    "/static/js/financial_chatbot.js",
    "/static/js/project.js",
    "/static/js/ai_settings.js",
]


def test_all_static_assets_are_served(client):
    for url in STATIC_ASSETS:
        response = client.get(url)
        assert response.status_code == 200, f"{url} -> {response.status_code}"
        assert response.content, f"{url} خالی است"


def test_scripts_referenced_by_templates_exist_on_disk(client):
    """هر اسکریپتی که قالبی بارگذاری می‌کند واقعاً روی دیسک موجود است."""
    import re
    from pathlib import Path

    templates = Path(WEB_DIR, "templates")
    referenced: set[str] = set()
    for template in templates.rglob("*.html"):
        referenced.update(re.findall(r'src="(/static/[^"]+)"', template.read_text(encoding="utf-8")))

    assert referenced, "هیچ فایل استاتیکی در قالب‌ها ارجاع نشده است"
    for url in sorted(referenced):
        response = client.get(url)
        assert response.status_code == 200, f"{url} در قالب ارجاع شده اما سرو نمی‌شود"


def test_static_assets_are_revalidated_not_cached_blindly(client):
    for url in STATIC_ASSETS:
        response = client.get(url)
        assert response.headers.get("cache-control") == "no-cache", url
        # ETag لازم است تا اعتبارسنجی مجدد ارزان (304) بماند
        assert response.headers.get("etag"), f"{url} هدر ETag ندارد"


def test_second_request_with_same_etag_is_a_cheap_304(client):
    """درخواست دوم با همان ETag باید 304 بگیرد (یعنی اعتبارسنجی، بایت دوباره نمی‌فرستد)."""
    first = client.get("/static/css/app.css")
    etag = first.headers["etag"]

    second = client.get("/static/css/app.css", headers={"If-None-Match": etag})
    assert second.status_code == 304


def test_missing_static_asset_returns_404(client):
    assert client.get("/static/js/does-not-exist.js").status_code == 404
