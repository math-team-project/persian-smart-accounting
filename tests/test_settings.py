"""تست‌های تنظیمات هوش مصنوعی هر کارگاه در هر پروژه (فاز تنظیمات).

سه چیزی که این فایل تضمین می‌کند:

۱) **زنجیره‌ی بازگشت به پیش‌فرض**: هر فیلدی که کاربر پر نکند (یا بعداً خالی کند)
   بی‌سروصدا از پیش‌فرض سامانه پر می‌شود و هیچ‌وقت خطا نمی‌دهد.
۲) **کلید API هرگز به متن خام ذخیره نمی‌شود و هرگز از هیچ endpoint ای برنمی‌گردد**
   -- نه خام، نه رمزشده؛ فقط ``api_key_set``.
۳) **اعتبارسنجی با پیام فارسی در هر دو مسیر «ذخیره» و «تست اتصال»**، و «تست اتصال»
   بدون هیچ فراخوانی واقعی شبکه (کلاینت مدل در تست‌ها جعلی است).

نکته‌ی محیطی: ``tests/conftest.py`` همه‌ی متغیرهای کلید LLM را پیش از ایمپورت
``api.main`` خالی می‌کند، بنابراین «پیش‌فرض سامانه» در تست‌ها قطعاً **بدون کلید**
است و نتیجه‌ی تست‌ها به ``.env`` واقعی توسعه‌دهنده وابسته نیست.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from api.db.models import WorkshopSetting
from api.repositories import projects as projects_repo
from api.repositories import workshop_runs as runs_repo
from api.services import ai_settings
from api.workshops.registry import get as get_workshop, workshops_with_settings

SLUG = "budget-analysis"
CIPHER_PREFIX = "fernet:v1:"

# کلید آزمایشی جعلی: هیچ سرویس واقعی این کلید را نمی‌شناسد و هیچ درخواست
# شبکه‌ای هم در این تست‌ها فرستاده نمی‌شود.
PLAIN_KEY = "sk-test-only-not-a-real-key-9f3ac1"


# ---------------------------------------------------------------------------
# کمک‌تابع‌ها
# ---------------------------------------------------------------------------
def _settings_url(project_id: int, slug: str = SLUG) -> str:
    return f"/api/projects/{project_id}/settings/{slug}"


def _list_url(project_id: int) -> str:
    return f"/api/projects/{project_id}/settings"


def _get_payload(client, project_id: int) -> dict:
    response = client.get(_list_url(project_id))
    assert response.status_code == 200, response.text
    return response.json()


def _panel(payload: dict, slug: str = SLUG) -> dict:
    panels = {item["slug"]: item for item in payload["workshops"]}
    assert slug in panels, f"کارگاه {slug} در پاسخ تنظیمات نیست"
    return panels[slug]


def _put(client, project_id: int, payload: dict, slug: str = SLUG):
    return client.put(_settings_url(project_id, slug), json=payload)


def _saved(client, project_id: int, payload: dict, slug: str = SLUG) -> dict:
    """پاسخ PUT تنظیمات: یک شیء «کارگاه» (نه فهرست کارگاه‌ها)."""
    response = _put(client, project_id, payload, slug)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# پیش‌فرض‌ها
# ---------------------------------------------------------------------------
def test_system_defaults_have_no_api_key_in_tests():
    """پیش‌فرض سامانه در محیط تست کلید ندارد (پیش‌نیاز قطعی‌بودن سایر تست‌ها)."""
    defaults = ai_settings.default_ai_settings()
    assert defaults.api_key is None
    assert defaults.api_url.startswith("http")
    assert defaults.model


def test_defaults_are_reported_when_nothing_is_saved(auth_client, db_session, project):
    defaults = ai_settings.default_ai_settings()

    payload = _get_payload(auth_client, project.id)
    assert payload["project_id"] == project.id
    assert payload["defaults"]["api_url"] == defaults.api_url
    assert payload["defaults"]["model"] == defaults.model
    assert payload["defaults"]["temperature"] == defaults.temperature
    assert payload["defaults"]["max_output_tokens"] == defaults.max_output_tokens
    assert payload["defaults"]["api_key_set"] is False

    panel = _panel(payload)
    # «هیچ چیزی ذخیره نشده» یعنی همه‌ی فیلدها خالی و کلید تنظیم‌شده وجود ندارد
    assert panel["api_key_set"] is False
    assert panel["api_url"] is None
    assert panel["model"] is None
    assert panel["temperature"] is None
    assert panel["max_output_tokens"] is None

    # و مقدار مؤثر دقیقاً همان پیش‌فرض سامانه است
    resolved = ai_settings.resolve_ai_settings(db_session, project.id, SLUG)
    assert resolved.api_url == defaults.api_url
    assert resolved.model == defaults.model
    assert resolved.api_key == defaults.api_key


def test_saved_values_win_and_empty_fields_fall_back_to_defaults(auth_client, db_session, project):
    saved = _saved(
        auth_client,
        project.id,
        {
            "api_url": "https://llm.example.invalid/v1",
            "model": "vendor/sample-model",
            "temperature": 0.5,
            "max_output_tokens": 3000,
        },
    )
    assert saved["api_url"] == "https://llm.example.invalid/v1"
    assert saved["model"] == "vendor/sample-model"
    assert saved["temperature"] == 0.5
    assert saved["max_output_tokens"] == 3000

    resolved = ai_settings.resolve_ai_settings(db_session, project.id, SLUG)
    assert resolved.api_url == "https://llm.example.invalid/v1"
    assert resolved.model == "vendor/sample-model"
    assert resolved.temperature == 0.5
    assert resolved.max_output_tokens == 3000

    # حالا همه‌ی فیلدها خالی می‌شوند: باید بی‌سروصدا به پیش‌فرض برگردد
    emptied = _saved(
        auth_client,
        project.id,
        {"api_url": "", "model": "", "temperature": None, "max_output_tokens": None},
    )
    assert emptied["api_url"] is None
    assert emptied["model"] is None
    assert emptied["temperature"] is None
    assert emptied["max_output_tokens"] is None

    defaults = ai_settings.default_ai_settings()
    resolved = ai_settings.resolve_ai_settings(db_session, project.id, SLUG)
    assert resolved.api_url == defaults.api_url
    assert resolved.model == defaults.model
    assert resolved.temperature == defaults.temperature
    assert resolved.max_output_tokens == defaults.max_output_tokens

    # ردیف تنظیماتِ کاملاً خالی در پایگاه‌داده باقی نمی‌ماند
    assert runs_repo.get_setting(db_session, project.id, SLUG) is None


def test_omitted_fields_reset_to_default_but_never_the_key(auth_client, db_session, project):
    """قرارداد «نیمهٔ راز» و «نیمهٔ غیرراز» عمداً متفاوت است:

    * فیلدهای غیرراز (آدرس/مدل/دما/طول خروجی): هر چه در درخواست بیاید ذخیره
      می‌شود؛ غایب یا خالی یعنی «پاک کن تا از پیش‌فرض سامانه استفاده شود».
    * کلید: غایب یا خالی یعنی «همان کلید قبلی را نگه دار» -- چون مقدار ذخیره‌شده
      هرگز به فرم برنمی‌گردد، پاک‌کردن کلید فقط با ``clear_api_key`` ممکن است.
    """
    _saved(
        auth_client,
        project.id,
        {
            "api_key": PLAIN_KEY,
            "api_url": "https://llm.example.invalid/v1",
            "model": "first",
        },
    )

    panel = _saved(auth_client, project.id, {"model": "second"})
    assert panel["model"] == "second"
    assert panel["api_url"] is None  # در این درخواست نیامده بود → به پیش‌فرض برگشت
    assert panel["api_key_set"] is True  # کلید اما دست‌نخورده ماند

    row = runs_repo.get_setting(db_session, project.id, SLUG)
    assert ai_settings.decrypt_secret(row.api_key) == PLAIN_KEY


# ---------------------------------------------------------------------------
# کلید API: رمزنگاری در حالت سکون و عدم افشا در هیچ پاسخ
# ---------------------------------------------------------------------------
def test_saved_api_key_is_encrypted_at_rest(auth_client, db_session, project):
    assert _saved(auth_client, project.id, {"api_key": PLAIN_KEY})["api_key_set"] is True

    row = runs_repo.get_setting(db_session, project.id, SLUG)
    assert row is not None and row.api_key
    assert row.api_key.startswith(CIPHER_PREFIX)
    assert PLAIN_KEY not in row.api_key

    # خروجی رمزگشایی‌شده دقیقاً همان کلید واردشده است (رفت‌وبرگشت سالم)
    assert ai_settings.decrypt_secret(row.api_key) == PLAIN_KEY
    assert ai_settings.resolve_ai_settings(db_session, project.id, SLUG).api_key == PLAIN_KEY

    # و متن خام در هیچ ستون دیگری هم تکرار نشده است
    dumped = json.dumps(
        {
            "api_key": row.api_key,
            "api_url": row.api_url,
            "model": row.model,
            "extra_settings": row.extra_settings,
        },
        ensure_ascii=False,
    )
    assert PLAIN_KEY not in dumped


def test_api_key_is_never_returned_by_any_endpoint(auth_client, project):
    saved = _saved(auth_client, project.id, {"api_key": PLAIN_KEY, "model": "vendor/sample-model"})
    # پاسخ خودِ «ذخیره» هم فقط وضعیت را برمی‌گرداند، نه مقدار کلید را
    serialized = json.dumps(saved, ensure_ascii=False)
    assert PLAIN_KEY not in serialized
    assert CIPHER_PREFIX not in serialized

    urls = [
        _list_url(project.id),
        f"/api/projects/{project.id}/jobs",
        f"/projects/{project.id}",
        f"/projects/{project.id}/history",
        f"/projects/{project.id}/{SLUG}",
        f"/projects/{project.id}/checklist",
        f"/projects/{project.id}/audit-summary",
        "/",
    ]
    for url in urls:
        response = auth_client.get(url)
        assert response.status_code == 200, f"{url} -> {response.status_code}"
        assert PLAIN_KEY not in response.text, f"کلید خام در پاسخ {url} لو رفت"
        assert CIPHER_PREFIX not in response.text, f"مقدار رمزشده در پاسخ {url} لو رفت"

    # قرارداد JSON: فقط api_key_set، بدون هیچ فیلد کلیدی
    panel = _panel(_get_payload(auth_client, project.id))
    assert "api_key" not in panel
    assert panel["api_key_set"] is True


def test_encrypted_key_cannot_be_read_with_a_different_encryption_key(auth_client, db_session, project, monkeypatch):
    """اگر کلید رمزنگاری سرور عوض شود، مقدار قدیمی «تنظیم‌نشده» تلقی می‌شود (نه خطا)."""
    _put(auth_client, project.id, {"api_key": PLAIN_KEY})
    assert ai_settings.resolve_ai_settings(db_session, project.id, SLUG).api_key == PLAIN_KEY

    # شبیه‌سازی عوض‌شدن کلید رمزنگاری سرور: فقط رمزگشایی ناموفق می‌شود.
    from cryptography.fernet import Fernet

    monkeypatch.setattr(ai_settings, "_load_fernet", lambda: Fernet(Fernet.generate_key()))

    resolved = ai_settings.resolve_ai_settings(db_session, project.id, SLUG)
    assert resolved.api_key is None  # بی‌سروصدا به پیش‌فرض سامانه برمی‌گردد


def test_legacy_plaintext_value_is_ignored_instead_of_used(db_session, project):
    """مقداری که قالب رمزشده ندارد (مثلاً داده‌ی قدیمی) استفاده نمی‌شود."""
    runs_repo.upsert_setting(db_session, project.id, SLUG, api_key="legacy-plain-text-key")

    resolved = ai_settings.resolve_ai_settings(db_session, project.id, SLUG)
    assert resolved.api_key is None  # یعنی پیش‌فرض سامانه (در تست: بدون کلید)


def test_empty_key_field_keeps_the_previously_saved_key(auth_client, db_session, project):
    """فیلد کلید هرگز با مقدار ذخیره‌شده پر نمی‌شود؛ پس خالی‌بودنش یعنی «نگه دار»."""
    _put(auth_client, project.id, {"api_key": PLAIN_KEY, "model": "first"})

    assert _saved(auth_client, project.id, {"api_key": "", "model": "second"})["api_key_set"] is True

    row = runs_repo.get_setting(db_session, project.id, SLUG)
    assert ai_settings.decrypt_secret(row.api_key) == PLAIN_KEY


def test_clear_api_key_returns_to_system_default(auth_client, db_session, project):
    _put(auth_client, project.id, {"api_key": PLAIN_KEY})

    assert _saved(auth_client, project.id, {"clear_api_key": True})["api_key_set"] is False

    row = runs_repo.get_setting(db_session, project.id, SLUG)
    assert row is None or not row.api_key
    resolved = ai_settings.resolve_ai_settings(db_session, project.id, SLUG)
    assert resolved.api_key == ai_settings.default_ai_settings().api_key


# ---------------------------------------------------------------------------
# اعتبارسنجی (پیام‌های فارسی) -- در هر دو مسیر «ذخیره» و «تست اتصال»
# ---------------------------------------------------------------------------
def test_temperature_outside_range_is_rejected_with_persian_message(auth_client, db_session, project):
    for value in (5, -1):
        response = _put(auth_client, project.id, {"temperature": value})
        assert response.status_code == 400
        assert "دما" in response.json()["detail"]

    assert runs_repo.get_setting(db_session, project.id, SLUG) is None


def test_max_output_tokens_outside_range_is_rejected_with_persian_message(auth_client, db_session, project):
    for value in (0, 200_001):
        response = _put(auth_client, project.id, {"max_output_tokens": value})
        assert response.status_code == 400
        assert "حداکثر طول خروجی" in response.json()["detail"]

    assert runs_repo.get_setting(db_session, project.id, SLUG) is None


def test_boundary_values_are_accepted(auth_client, project):
    panel = _saved(auth_client, project.id, {"temperature": 0, "max_output_tokens": 1})
    assert panel["temperature"] == 0
    assert panel["max_output_tokens"] == 1


# ---------------------------------------------------------------------------
# جداسازی: بین پروژه‌ها، بین کارگاه‌ها و بین کاربران
# ---------------------------------------------------------------------------
def test_settings_are_scoped_to_one_project(auth_client, db_session, project):
    other = projects_repo.create(db_session, project.user_id, "پروژهٔ دوم")

    _put(auth_client, project.id, {"model": "only-first-project"})

    assert _panel(_get_payload(auth_client, project.id))["model"] == "only-first-project"
    assert _panel(_get_payload(auth_client, other.id))["model"] is None
    assert runs_repo.get_setting(db_session, other.id, SLUG) is None


def test_settings_are_scoped_to_one_workshop(db_session, project):
    """هر کارگاه ردیف تنظیمات خودش را دارد (و کلید کارگاه الف در کارگاه ب دیده نمی‌شود)."""
    ai_settings.save_ai_settings(db_session, project.id, "budget-analysis", api_key=PLAIN_KEY)
    ai_settings.save_ai_settings(db_session, project.id, "checklist", model="checklist-only")

    budget = ai_settings.resolve_ai_settings(db_session, project.id, "budget-analysis")
    checklist = ai_settings.resolve_ai_settings(db_session, project.id, "checklist")
    assert budget.api_key == PLAIN_KEY
    assert checklist.api_key is None
    assert checklist.model == "checklist-only"
    assert budget.model != "checklist-only"
    # کارگاهی که هیچ ردیفی ندارد هم فقط پیش‌فرض می‌گیرد
    assert ai_settings.resolve_ai_settings(db_session, project.id, "audit-summary").api_key is None


def test_settings_endpoints_require_login(client, project):
    assert client.get(_list_url(project.id)).status_code == 401
    assert client.put(_settings_url(project.id), json={"model": "x"}).status_code == 401
    assert client.post(_settings_url(project.id) + "/test", json={}).status_code == 401


def test_settings_of_other_users_project_are_not_reachable(client, db_session, second_user, project):
    client.post("/login", data={"username": "other", "password": "secret123"}, follow_redirects=False)

    assert client.get(_list_url(project.id)).status_code == 404
    assert client.put(_settings_url(project.id), json={"model": "x"}).status_code == 404


def test_only_workshops_with_applied_settings_are_configurable(auth_client, project):
    payload = _get_payload(auth_client, project.id)
    # رجیستری تعیین می‌کند کدام کارگاه فرم تنظیمات دارد (settings_applied). الآن دو
    # کارگاه این شرط را دارند: تحلیل بودجه (تنظیمات حل‌شده را در شروع اجرا می‌گیرد)
    # و چت‌بات مالی (در هر پرسش، کلاینت مدل را از همین زنجیره می‌سازد). این مجموعه
    # مستقیماً از رجیستری خوانده می‌شود تا افزودن کارگاه بعدی فقط یک ورودی رجیستری
    # باشد، نه یک ویرایش دوباره در این تست.
    expected = sorted(
        workshop.slug for workshop in workshops_with_settings()
    )
    assert sorted(item["slug"] for item in payload["workshops"]) == expected
    assert expected == ["budget-analysis", "financial_chatbot"]

    not_configurable = _put(auth_client, project.id, {"model": "x"}, slug="checklist")
    assert not_configurable.status_code == 404
    assert "تنظیمات" in not_configurable.json()["detail"]

    unknown = _put(auth_client, project.id, {"model": "x"}, slug="not-a-workshop")
    assert unknown.status_code == 404


# ---------------------------------------------------------------------------
# تست اتصال (بدون شبکه: کلاینت مدل جعلی است)
# ---------------------------------------------------------------------------
class _FakeLLM:
    """کلاینت جعلی مدل: هیچ درخواستی به شبکه نمی‌فرستد."""

    def __init__(self, settings, *, payload=None, error=None):
        self.settings = settings
        self._payload = payload
        self._error = error
        self.calls: list[list[dict]] = []

    def complete_json(self, messages):
        self.calls.append(messages)
        if self._error is not None:
            raise self._error
        return self._payload


@pytest.fixture()
def fake_llm(monkeypatch):
    """``BudgetAnalysisLLM`` را در سرویس کارگاه با کلاس جعلی جایگزین می‌کند.

    خروجی یک وضعیت قابل‌تغییر است: ``state["error"]`` را برای شبیه‌سازی شکست
    اتصال تغییر دهید و از ``state["instances"]`` نمونه‌های ساخته‌شده را بخوانید.
    """
    from api.services import budget_service

    state = {"payload": {"ok": True}, "error": None, "instances": []}

    def _factory(settings):
        instance = _FakeLLM(settings, payload=state["payload"], error=state["error"])
        state["instances"].append(instance)
        return instance

    monkeypatch.setattr(budget_service, "BudgetAnalysisLLM", _factory)
    return state


def test_connection_test_reports_success_in_persian(auth_client, project, fake_llm):
    response = auth_client.post(_settings_url(project.id) + "/test", json={"api_key": PLAIN_KEY})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert "اتصال موفق" in body["message"]


def test_connection_test_uses_unsaved_form_values_without_persisting_them(
    auth_client, db_session, project, fake_llm
):
    response = auth_client.post(
        _settings_url(project.id) + "/test",
        json={"api_url": "https://llm.example.invalid/v1", "model": "vendor/probe-model"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True

    probe = fake_llm["instances"][-1].settings
    assert probe.base_url == "https://llm.example.invalid/v1"
    assert probe.model == "vendor/probe-model"
    # کلید از فرم نیامده و چیزی هم ذخیره نشده است
    assert probe.api_key is None

    # «تست اتصال» هیچ چیزی را ذخیره نمی‌کند
    assert runs_repo.get_setting(db_session, project.id, SLUG) is None


def test_connection_test_reports_failure_in_persian(auth_client, project, fake_llm):
    from budget_analysis.llm import BudgetLLMError

    fake_llm["error"] = BudgetLLMError("سرویس پاسخ نداد.")

    response = auth_client.post(_settings_url(project.id) + "/test", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    assert "اتصال برقرار نشد" in body["message"]
    assert "سرویس پاسخ نداد." in body["message"]


def test_connection_test_rejects_invalid_numbers_before_any_model_call(auth_client, project, fake_llm):
    """ورودی نامعتبر باید با همان پیام «ذخیره» و پیش از هر تماس با مدل رد شود."""
    for payload, keyword in (({"temperature": 9}, "دما"), ({"max_output_tokens": 0}, "حداکثر طول خروجی")):
        response = auth_client.post(_settings_url(project.id) + "/test", json=payload)
        assert response.status_code == 400
        assert keyword in response.json()["detail"]

    assert fake_llm["instances"] == []


def test_connection_test_is_unavailable_for_workshops_without_one(auth_client, project):
    response = auth_client.post(f"/api/projects/{project.id}/settings/checklist/test", json={})
    assert response.status_code == 404


def test_panel_context_exposes_no_secret(auth_client, db_session, project):
    """قالب فرم تنظیمات صفحهٔ پروژه فقط نشانگر ماسک‌شده دارد، نه خود کلید."""
    _put(auth_client, project.id, {"api_key": PLAIN_KEY})

    workshop = get_workshop(SLUG)
    assert workshop is not None
    panel = ai_settings.panel_for(db_session, project.id, workshop)
    assert panel["api_key_set"] is True
    assert "api_key" not in panel

    serialized = json.dumps(panel, ensure_ascii=False, default=str)
    assert PLAIN_KEY not in serialized
    assert ai_settings.MASKED_KEY_PLACEHOLDER_FA in serialized

    page = auth_client.get(f"/projects/{project.id}")
    assert page.status_code == 200
    assert ai_settings.MASKED_KEY_PLACEHOLDER_FA in page.text
    assert PLAIN_KEY not in page.text


def test_setting_row_is_removed_when_everything_is_cleared(auth_client, db_session, project):
    _put(auth_client, project.id, {"api_key": PLAIN_KEY, "model": "m"})
    assert db_session.scalars(
        select(WorkshopSetting).where(WorkshopSetting.project_id == project.id)
    ).all() != []

    _put(auth_client, project.id, {"clear_api_key": True, "api_key": "", "model": ""})
    db_session.expire_all()
    assert db_session.scalars(
        select(WorkshopSetting).where(WorkshopSetting.project_id == project.id)
    ).all() == []
