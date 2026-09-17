"""تست‌های صفحه‌های خطا و تفکیک «مسیر صفحه» از «مسیر API».

قرارداد:

* مسیرهای HTML (هر چیزی که زیر ``/api/`` نباشد) در برابر خطاهای قابل‌پیش‌بینی
  (۴۰۴، ۴۰۳ و...) یک **صفحه‌ی فارسی** با راه بازگشت می‌گیرند -- نه JSON خام.
* مسیرهای JSON زیر ``/api/`` دقیقاً همان پاسخ ساخت‌یافته‌ی ``{"detail": ...}`` را
  نگه می‌دارند، چون کلاینت‌های جاوااسکریپت روی همان کلید سوارند.

هدف: هیچ کاربری نباید در برابر یک نشانی قدیمی/اشتباه، متن خام JSON یا پیام
انگلیسی (``Not Found``) ببیند.
"""
from __future__ import annotations

import re

from api.routers.auth import WRONG_CREDENTIALS


def _main_text(response) -> str:
    """متن قابل‌مشاهده‌ی بدنه‌ی پاسخ، فشرده‌شده برای مقایسه."""
    text = re.sub(r"<script.*?</script>", " ", response.text, flags=re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def test_page_route_404_renders_persian_html_not_json(auth_client, project):
    response = auth_client.get(f"/projects/{project.id}/not-a-real-workshop")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "{" not in response.text[:200]

    body = _main_text(response)
    assert "این صفحه پیدا نشد" in body
    assert "کارگاه مورد نظر یافت نشد." in body  # پیام اختصاصی خودِ روتر
    assert "۴۰۴" in body  # کد وضعیت با ارقام فارسی
    assert "بازگشت به فهرست پروژه‌ها" in body  # راه بازگشت
    assert "Not Found" not in body


def test_unknown_path_renders_persian_page_without_english_text(auth_client):
    response = auth_client.get("/totally-unknown-path")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")

    body = _main_text(response)
    assert "این صفحه پیدا نشد" in body
    assert "شده است" in body
    assert "Not Found" not in body
    assert "detail" not in body


def test_error_page_keeps_the_signed_in_shell(auth_client):
    """کاربر واردشده در صفحه‌ی خطا هم پوسته‌ی واردشده (فرم خروج) را می‌بیند."""
    response = auth_client.get("/totally-unknown-path")
    body = _main_text(response)
    assert "پروژه‌ها" in body
    # فرم خروج فقط برای کاربر واردشده در پوسته رندر می‌شود
    assert 'action="/logout"' in response.text


def test_error_page_for_anonymous_visitor_has_no_logout_form(client):
    response = client.get("/totally-unknown-path")
    assert "این صفحه پیدا نشد" in _main_text(response)
    assert 'action="/logout"' not in response.text


def test_api_routes_keep_json_error_contract(auth_client, project):
    """مسیرهای ``/api/`` عیناً همان ``detail`` فارسی را برمی‌گردانند (نه HTML)."""
    response = auth_client.get("/api/projects/999999/jobs")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "پروژه یافت نشد."}


def test_api_requires_login_returns_json_not_a_redirect(client, project):
    """نبود نشست روی مسیر JSON یعنی 401 ساخت‌یافته (نه هدایت به صفحه‌ی ورود)."""
    response = client.get(f"/api/projects/{project.id}/jobs")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")
    assert "detail" in response.json()


def test_login_page_still_reports_bad_credentials_inline(client, user):
    """خطای فرم ورود همچنان داخل خود صفحه‌ی ورود نمایش داده می‌شود، نه صفحه‌ی خطا."""
    response = client.post("/login", data={"username": "tester", "password": "wrong"})
    assert response.status_code == 400
    assert WRONG_CREDENTIALS in response.text
    assert "این صفحه پیدا نشد" not in response.text


def test_delete_confirmation_error_renders_page_for_form_posts(auth_client, project):
    """ارسال فرم بدون فیلد تأیید: پیام فارسی در قالب صفحه، نه JSON خام."""
    response = auth_client.post(f"/projects/{project.id}/delete", data={})
    assert response.status_code == 400
    assert "تأیید حذف نامعتبر است." in _main_text(response)


def test_unauthenticated_page_request_redirects_to_login_not_error_page(client, project):
    """نبود نشست یعنی «هدایت به ورود»، نه صفحه‌ی خطا."""
    response = client.get(f"/projects/{project.id}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?next=")


def test_docs_endpoints_are_unaffected(auth_client):
    """مسیرهای مستندات FastAPI نباید به دست handler خطای صفحه‌ای بیفتند."""
    assert auth_client.get("/openapi.json").status_code == 200
    assert auth_client.get("/docs").status_code == 200
