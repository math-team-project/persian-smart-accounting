"""تست‌های کارگاه «چت‌بات مالی» (گفتگوهای متادیتا-محور + پرسش/پاسخ).

راهبرد تست، مثل بقیه‌ی کارگاه‌های این پروژه: هیچ LLM، embedder، Chroma store یا
شبکه‌ای درگیر نمی‌شود. در بیشتر تست‌ها ``financial_chatbot_service.ask_with_history``
با یک نسخه‌ی جعلی جایگزین می‌شود (یعنی مرز سرویس -- دقیقاً همان‌جایی که تست‌های
کارگاه‌های دیگر pipeline را جایگزین می‌کنند) و در یک تست جدا، خودِ سرویس واقعی با
``rag_ai_adapter`` جعلی اجرا می‌شود تا سیم‌کشی و شکل خروجی‌اش هم آزموده شود.

سه چیز که این فایل به‌طور خاص تضمین می‌کند:

۱) **جداسازی**: یک گفتگو فقط با ``(project_id, user_id)`` خودش خوانده/حذف می‌شود؛
   پروژه/کاربر دیگر آن را نمی‌بیند (۴۰۴، نه ۴۰۳).
۲) **دروازه‌بندی**: تا وقتی ``projects.latest_ready_kb_id`` خالی است، صفحه پیام
   فارسی راهنما نشان می‌دهد (نه رابط چت) و پرسش با پیام فارسی رد می‌شود.
۳) **بی‌حالتی سرور**: هیچ متن گفتگویی ذخیره نمی‌شود؛ هر پاسخ فقط از
   ``recent_history`` همان درخواست ساخته می‌شود و دو پرسش هم‌زمان روی دو گفتگوی
   مختلف پروژه با هم قاطی نمی‌شوند.

درباره‌ی بررسی XSS (تست آخر): این پروژه Markdown پاسخ مدل را **سمت مرورگر** و با
یک تابع ``escape-first`` رندر می‌کند (``web/static/js/financial_chatbot.js``)، پس
``TestClient`` که جاوااسکریپت اجرا نمی‌کند نمی‌تواند خروجی نهایی مرورگر را بسنجد.
آن تست به‌جای ادعای نادرست، دو چیز واقعاً قابل‌سنجش را قفل می‌کند: (الف) پاسخ
مورد مدل به‌صورت ``application/json`` منتقل می‌شود (یعنی هرگز به‌عنوان HTML
تفسیر/اجرا نمی‌شود) و متن مدل دست‌نخورده در JSON برمی‌گردد؛ (ب) منبع جاوااسکریپت
ترتیب «فرار دادن پیش از تبدیل» را رعایت می‌کند و مسیر تزریق پاسخ به DOM از همان
تابع می‌گذرد.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import WEB_DIR, app
from api.repositories import chat_sessions as chat_repo
from api.repositories import knowledge_bases as kb_repo
from api.repositories import projects as projects_repo
from api.services import financial_chatbot_service as chatbot
from api.services import rag_ai_adapter

SLUG = "financial_chatbot"
JS_PATH = Path(WEB_DIR, "static", "js", "financial_chatbot.js")
TEMPLATE_PATH = Path(WEB_DIR, "templates", "financial_chatbot.html")

USERNAME = "tester"
PASSWORD = "secret123"


# ---------------------------------------------------------------------------
# ابزارها
# ---------------------------------------------------------------------------
def _api(project_id: int) -> str:
    return f"/api/projects/{project_id}/{SLUG}"


def _make_ready_kb(db_session, *, project_id: int, user_id: int) -> str:
    """یک پایگاه‌دانش ``ready`` می‌سازد و اشاره‌گر پروژه را به آن می‌بندد.

    ``latest_ready_kb_id`` یک کلید خارجی واقعی است (و ``PRAGMA foreign_keys=ON``
    فعال است)، بنابراین نمی‌توان آن را به یک شناسه‌ی ساختگی تنظیم کرد -- دقیقاً
    همان چیزی که مصرف‌کننده‌ها هم باید رعایت کنند.
    """
    kb = kb_repo.create(
        db_session,
        project_id=project_id,
        user_id=user_id,
        embedding_model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        embedding_device="cpu",
        chroma_collection_name="kb-chatbot-test",
        chroma_persist_dir="1/1/chatbot-test",
    )
    kb_repo.mark_ready_and_update_project_pointer(db_session, kb.id)
    return kb.id


@pytest.fixture()
def ready_project(db_session, user):
    """پروژه‌ای که کارگاه چک‌لیست برایش با موفقیت اجرا شده (پایگاه‌دانش آماده دارد).

    عمداً یک پروژه‌ی *مستقل* می‌سازد (و نه روی پروژه‌ی fixture ``project``) تا
    تست‌هایی که هر دو را می‌خواهند واقعاً دو پروژه‌ی متفاوت داشته باشند -- همان
    چیزی که برای سنجش دروازه‌بندی و جداسازی لازم است.
    """
    project = projects_repo.create(db_session, user.id, "پروژهٔ دارای پایگاه‌دانش")
    _make_ready_kb(db_session, project_id=project.id, user_id=user.id)
    db_session.expire_all()
    return project


@pytest.fixture()
def other_user_project(db_session, second_user):
    return projects_repo.create(db_session, second_user.id, "پروژه‌ی کاربر دیگر")


# ---------------------------------------------------------------------------
# گفتگوها: ساخت / فهرست / حذف
# ---------------------------------------------------------------------------
def test_create_session_returns_metadata_only(auth_client, project):
    response = auth_client.post(_api(project.id) + "/sessions", json={"title": "بررسی موجودی"})

    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "بررسی موجودی"
    assert isinstance(data["id"], int)
    assert data["created_at"] and data["updated_at"]
    # قرارداد «فقط متادیتا»: هیچ کلید دیگری (مثل پیام‌ها) در پاسخ نیست.
    assert set(data) == {"id", "title", "created_at", "updated_at"}


def test_create_session_without_title_uses_a_persian_default(auth_client, project):
    response = auth_client.post(_api(project.id) + "/sessions", json={})

    assert response.status_code == 201
    assert response.json()["title"] == chat_repo.DEFAULT_TITLE_FA


def test_list_sessions_is_empty_for_a_fresh_project(auth_client, project):
    response = auth_client.get(_api(project.id) + "/sessions")

    assert response.status_code == 200
    assert response.json() == {"sessions": []}


def test_list_sessions_returns_newest_used_first(auth_client, project):
    """ترتیب فهرست قطعی است: جدیدترین «آخرین استفاده» اول، و در زمان برابری id نزولی."""
    first = auth_client.post(_api(project.id) + "/sessions", json={"title": "اول"}).json()
    second = auth_client.post(_api(project.id) + "/sessions", json={"title": "دوم"}).json()
    assert first["id"] != second["id"]

    listed = auth_client.get(_api(project.id) + "/sessions").json()["sessions"]
    assert [item["id"] for item in listed] == [second["id"], first["id"]]
    assert [item["title"] for item in listed] == ["دوم", "اول"]


def test_ask_touches_the_session_so_it_moves_to_the_top(
    auth_client, ready_project, monkeypatch
):
    first = auth_client.post(
        _api(ready_project.id) + "/sessions", json={"title": "اول"}
    ).json()
    second = auth_client.post(
        _api(ready_project.id) + "/sessions", json={"title": "دوم"}
    ).json()
    assert second["id"] > first["id"]

    monkeypatch.setattr(
        "api.workshops.financial_chatbot.chatbot.ask_with_history",
        _fake_ask(answer="پاسخ آزمایشی"),
    )
    # گفتگوی قدیمی‌تر (اول) دوباره استفاده می‌شود.
    response = auth_client.post(
        _api(ready_project.id) + f"/sessions/{first['id']}/ask", json={"question": "سؤال"}
    )
    assert response.status_code == 200

    listed = auth_client.get(_api(ready_project.id) + "/sessions").json()["sessions"]
    assert listed[0]["id"] == first["id"]


def test_delete_session_removes_only_that_row(auth_client, project, db_session):
    keep = auth_client.post(_api(project.id) + "/sessions", json={"title": "بماند"}).json()
    remove = auth_client.post(_api(project.id) + "/sessions", json={"title": "حذف"}).json()

    response = auth_client.delete(_api(project.id) + f"/sessions/{remove['id']}")

    assert response.status_code == 204
    remaining = [item["id"] for item in auth_client.get(_api(project.id) + "/sessions").json()["sessions"]]
    assert remaining == [keep["id"]]
    # ردیف واقعاً از پایگاه‌داده رفته است (حذف سخت، نه علامت‌گذاری).
    assert (
        chat_repo.get_by_id(
            db_session, remove["id"], user_id=project.user_id, project_id=project.id
        )
        is None
    )


def test_delete_session_of_another_project_returns_404_and_keeps_the_row(
    auth_client, project, other_user_project, db_session, second_user
):
    chat = chat_repo.create(
        db_session, project_id=other_user_project.id, user_id=second_user.id, title="مال دیگری"
    )

    response = auth_client.delete(_api(project.id) + f"/sessions/{chat.id}")

    assert response.status_code == 404
    assert chat_repo.get_by_id(
        db_session, chat.id, user_id=second_user.id, project_id=other_user_project.id
    ) is not None


def test_sessions_are_never_visible_through_another_project(
    auth_client, project, ready_project, db_session, user
):
    """گفتگوی پروژه‌ی «آماده» از مسیر پروژه‌ی دیگر (همان کاربر) هم دیده نمی‌شود."""
    chat = chat_repo.create(
        db_session, project_id=ready_project.id, user_id=user.id, title="فقط همین‌جا"
    )

    listed = auth_client.get(_api(project.id) + "/sessions").json()["sessions"]
    assert listed == []

    stored = db_session.get(type(chat), chat.id)
    assert stored is not None  # حذف/جابه‌جا نشده است


def test_chat_session_repo_isolates_users_and_projects(db_session, project, user, second_user):
    other_project = projects_repo.create(db_session, second_user.id, "پروژه‌ی دیگر")
    mine = chat_repo.create(db_session, project_id=project.id, user_id=user.id, title="مال من")
    theirs = chat_repo.create(
        db_session, project_id=other_project.id, user_id=second_user.id, title="مال دیگری"
    )

    assert [c.id for c in chat_repo.list_by_project(db_session, project.id, user_id=user.id)] == [mine.id]
    # همان پروژه اما کاربر دیگر: هیچ ردیفی برنمی‌گردد.
    assert chat_repo.list_by_project(db_session, project.id, user_id=second_user.id) == []
    assert (
        chat_repo.get_by_id(db_session, theirs.id, user_id=user.id, project_id=project.id) is None
    )
    # حذف با شناسه‌ی درست اما مالکیت نادرست، هیچ چیزی را پاک نمی‌کند.
    assert (
        chat_repo.delete_by_id(db_session, theirs.id, user_id=user.id, project_id=project.id) is False
    )
    assert chat_repo.get_by_id(
        db_session, theirs.id, user_id=second_user.id, project_id=other_project.id
    ) is not None


def test_deleting_a_project_also_removes_its_chat_sessions(db_session, project, user):
    """حذف پروژه نباید ردیف یتیم گفتگو باقی بگذارد (کلید خارجی CASCADE).

    این تست فقط رفتار *فعلی* را قفل می‌کند؛ فاز بعدی (حذف پروژه) همین حذف را
    صریح و همراه با گزارش تعداد انجام می‌دهد.
    """
    chat_repo.create(db_session, project_id=project.id, user_id=user.id, title="یتیم‌نشو")
    projects_repo.delete(db_session, project)

    remaining = db_session.query(chat_repo.ChatSession).all()
    assert remaining == []


# ---------------------------------------------------------------------------
# دروازه‌بندی: بدون پایگاه‌دانش آماده
# ---------------------------------------------------------------------------
def test_page_shows_the_gate_message_when_no_knowledge_base_exists(auth_client, project):
    response = auth_client.get(f"/projects/{project.id}/{SLUG}")

    assert response.status_code == 200
    body = response.text
    assert chatbot.GATED_MESSAGE_FA in body
    # پیوند به کارگاه چک‌لیست (از رجیستری، نه هاردکد در قالب)
    assert f"/projects/{project.id}/checklist" in body
    # رابط چت و اسکریپت آن در این حالت بارگذاری نمی‌شوند.
    assert 'id="psa-chat-form"' not in body
    assert "/static/js/financial_chatbot.js" not in body
    # ... اما کارگاه از ناوبری/کارت‌ها پنهان نشده است.
    assert "چت‌بات مالی" in body


def test_page_shows_the_chat_interface_once_a_knowledge_base_is_ready(auth_client, ready_project):
    response = auth_client.get(f"/projects/{ready_project.id}/{SLUG}")

    assert response.status_code == 200
    body = response.text
    assert 'id="psa-chat-form"' in body
    assert "/static/js/financial_chatbot.js" in body
    assert chatbot.GATED_MESSAGE_FA not in body
    # قرارداد ``window.PSA_WORKSHOP`` با کلید ذخیره‌سازی مخصوص همین پروژه.
    assert f'"psa.financial_chatbot.sessionId.p{ready_project.id}"' in body


def test_ask_is_refused_with_a_persian_message_when_not_available(auth_client, project):
    """حتی با یک گفتگوی معتبر، پرسش بدون پایگاه‌دانش آماده رد می‌شود."""
    chat = auth_client.post(_api(project.id) + "/sessions", json={"title": "خاموش"}).json()

    response = auth_client.post(
        _api(project.id) + f"/sessions/{chat['id']}/ask", json={"question": "چقدر بود؟"}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == chatbot.GATED_MESSAGE_FA


def test_ask_refuses_a_session_of_another_users_project(
    auth_client, project, other_user_project, db_session, second_user, monkeypatch
):
    chat = chat_repo.create(
        db_session, project_id=other_user_project.id, user_id=second_user.id, title="مال دیگری"
    )
    monkeypatch.setattr(
        "api.workshops.financial_chatbot.chatbot.ask_with_history",
        _fake_ask(answer="نباید دیده شود"),
    )

    response = auth_client.post(
        _api(project.id) + f"/sessions/{chat.id}/ask", json={"question": "سؤال"}
    )

    assert response.status_code == 404


def test_ask_requires_an_existing_session(auth_client, ready_project):
    response = auth_client.post(
        _api(ready_project.id) + "/sessions/999999/ask", json={"question": "سؤال"}
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# پرسش: مسیر API با سرویس جعلی (بدون LLM/embedder/Chroma)
# ---------------------------------------------------------------------------
def _as_dicts(recent_history) -> list[dict]:
    """نوبت‌های دریافتی سرویس را به دیکشنری ساده تبدیل می‌کند.

    مسیر واقعی: روتر بدنه‌ی JSON را با Pydantic اعتبارسنجی می‌کند و همان
    ``ChatTurn``ها را به سرویس می‌دهد (نه دیکشنری خام). ``compose_question``
    هر دو شکل را می‌پذیرد؛ این کمک‌تابع فقط مقایسه در تست را ساده می‌کند.
    """
    return [
        {"role": turn.role, "content": turn.content}
        if hasattr(turn, "role")
        else {"role": turn.get("role"), "content": turn.get("content")}
        for turn in (recent_history or [])
    ]


def _fake_ask(*, answer="پاسخ جعلی", confidence="high", sources=None, route_reasoning="", history_turns_used=None):
    """یک ``ask_with_history`` جعلی که استدلال‌هایش را هم ثبت می‌کند."""
    calls: list[dict] = []

    def _inner(session, project, question, recent_history=None):
        history = _as_dicts(recent_history)
        calls.append({"project_id": project.id, "question": question, "history": history})
        return {
            "answer": answer,
            "confidence": confidence,
            "sources": sources or [],
            "route_reasoning": route_reasoning,
            "history_turns_used": len(history) if history_turns_used is None else history_turns_used,
        }

    _inner.calls = calls  # type: ignore[attr-defined]
    return _inner


def test_ask_returns_the_expected_shape(auth_client, ready_project, monkeypatch):
    fake = _fake_ask(
        answer="**موجودی نقد** برابر ۱۰۰ است.",
        confidence="medium",
        sources=[
            {
                "file_key": "financial_statements",
                "sheet_names": ["ترازنامه"],
                "chunk_id": "c1",
                "similarity": 0.8123,
                "snippet": "موجودی نقد | 100",
            }
        ],
        route_reasoning="شیت مرتبط پیدا شد",
    )
    monkeypatch.setattr("api.workshops.financial_chatbot.chatbot.ask_with_history", fake)
    chat = auth_client.post(_api(ready_project.id) + "/sessions", json={"title": "شکل"}).json()

    response = auth_client.post(
        _api(ready_project.id) + f"/sessions/{chat['id']}/ask",
        json={"question": "موجودی نقد چقدر است؟", "recent_history": []},
    )

    assert response.status_code == 200
    data = response.json()
    assert set(data) == {
        "answer",
        "confidence",
        "sources",
        "route_reasoning",
        "history_turns_used",
    }
    assert data["confidence"] == "medium"
    assert data["sources"][0]["file_key"] == "financial_statements"
    assert data["route_reasoning"] == "شیت مرتبط پیدا شد"
    assert data["history_turns_used"] == 0
    # پاسخ دست‌نخورده به‌عنوان داده منتقل می‌شود (هیچ HTML/escape سمت سرور).
    assert data["answer"] == "**موجودی نقد** برابر ۱۰۰ است."
    assert fake.calls[0]["question"] == "موجودی نقد چقدر است؟"


def test_ask_forwards_the_recent_history_of_this_request_only(auth_client, ready_project, monkeypatch):
    fake = _fake_ask()
    monkeypatch.setattr("api.workshops.financial_chatbot.chatbot.ask_with_history", fake)
    chat = auth_client.post(_api(ready_project.id) + "/sessions", json={"title": "زمینه"}).json()

    history = [
        {"role": "user", "content": "پرسش اول"},
        {"role": "assistant", "content": "پاسخ اول"},
    ]
    response = auth_client.post(
        _api(ready_project.id) + f"/sessions/{chat['id']}/ask",
        json={"question": "پرسش دوم", "recent_history": history},
    )

    assert response.status_code == 200
    assert response.json()["history_turns_used"] == 2
    assert fake.calls[0]["history"] == history


def test_two_concurrent_asks_never_share_history(auth_client, ready_project, monkeypatch):
    """بی‌حالتی سرور: دو پرسش هم‌زمان روی دو گفتگوی مختلف، هرکدام فقط زمینه‌ی خودش را می‌بیند.

    برای اینکه «هم‌زمانی» واقعاً اتفاق بیفتد (نه فقط پشت‌سرهم)، تابع جعلی روی یک
    ``Barrier`` می‌ایستد تا هر دو درخواست به هم برسند. اگر موتور تست نتواند دو
    درخواست را واقعاً موازی اجرا کند، سد با تایم‌اوت باز می‌شود و تست همچنان
    معتبر می‌ماند (ویژگی اصلی -- «هر پاسخ فقط از payload خودش ساخته می‌شود» --
    مستقل از موازی‌بودن واقعی سنجیده می‌شود).
    """
    session_a = auth_client.post(_api(ready_project.id) + "/sessions", json={"title": "الف"}).json()
    session_b = auth_client.post(_api(ready_project.id) + "/sessions", json={"title": "ب"}).json()

    barrier = threading.Barrier(2, timeout=5)
    seen: dict[str, list] = {}
    lock = threading.Lock()

    def _inner(session, project, question, recent_history=None):
        history = _as_dicts(recent_history)
        try:
            barrier.wait()
        except threading.BrokenBarrierError:  # pragma: no cover - اجرای غیرموازی
            pass
        with lock:
            seen[question] = history
        # پاسخ «اثر انگشت» زمینه‌ی همان درخواست است: هیچ حالت مشترکی وجود ندارد.
        return {
            "answer": f"پاسخ برای {question} با {len(history)} نوبت زمینه",
            "confidence": "high",
            "sources": [],
            "route_reasoning": "",
            "history_turns_used": len(history),
        }

    monkeypatch.setattr("api.workshops.financial_chatbot.chatbot.ask_with_history", _inner)

    history_a = [{"role": "user", "content": "متن مخصوص الف"}]
    history_b = [
        {"role": "user", "content": "متن مخصوص ب"},
        {"role": "assistant", "content": "پاسخ مخصوص ب"},
        {"role": "user", "content": "پیگیری مخصوص ب"},
    ]

    def _call(session_id: int, question: str, history: list) -> dict:
        # هر ترد کلاینت خودش را می‌سازد تا یک portal مشترک بین دو ترد به کار نیفتد.
        with TestClient(app) as local_client:
            login = local_client.post(
                "/login", data={"username": USERNAME, "password": PASSWORD}, follow_redirects=False
            )
            assert login.status_code == 303
            response = local_client.post(
                _api(ready_project.id) + f"/sessions/{session_id}/ask",
                json={"question": question, "recent_history": history},
            )
            assert response.status_code == 200
            return response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(_call, session_a["id"], "پرسش الف", history_a)
        future_b = pool.submit(_call, session_b["id"], "پرسش ب", history_b)
        result_a = future_a.result(timeout=30)
        result_b = future_b.result(timeout=30)

    assert result_a["answer"] == "پاسخ برای پرسش الف با 1 نوبت زمینه"
    assert result_b["answer"] == "پاسخ برای پرسش ب با 3 نوبت زمینه"
    assert result_a["history_turns_used"] == 1
    assert result_b["history_turns_used"] == 3
    assert seen["پرسش الف"] == history_a
    assert seen["پرسش ب"] == history_b


def test_ask_rejects_an_empty_question(auth_client, ready_project, monkeypatch):
    monkeypatch.setattr(
        "api.workshops.financial_chatbot.chatbot.ask_with_history",
        _fake_ask(),
    )
    chat = auth_client.post(_api(ready_project.id) + "/sessions", json={"title": "خالی"}).json()

    response = auth_client.post(
        _api(ready_project.id) + f"/sessions/{chat['id']}/ask", json={"question": ""}
    )

    # سقف/کف اعتبارسنجی Pydantic پیش از رسیدن به سرویس جلوی پرسش خالی را می‌گیرد.
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# سرویس واقعی: سیم‌کشی با adapter جعلی (بدون LLM/embedder/Chroma واقعی)
# ---------------------------------------------------------------------------
def test_ask_with_history_builds_the_kb_and_llm_client_from_the_adapter(
    db_session, ready_project, monkeypatch
):
    """سرویس واقعی، اما همه‌ی اشیای سنگین از ``rag_ai_adapter`` جعلی می‌آیند."""
    captured: dict[str, object] = {}

    class _FakeKnowledgeBase:
        def list_sheets(self):
            return {"financial_statements": ["ترازنامه"]}

    read_only_kb = _FakeKnowledgeBase()
    llm_client = object()

    def _fake_readonly(user_id, project_id, kb_id, **kwargs):
        captured["readonly"] = (user_id, project_id, kb_id)
        return read_only_kb

    def _fake_llm_client(session, project_id, workshop_slug=None, **kwargs):
        captured["llm"] = (project_id, workshop_slug)
        return llm_client

    def _fake_chat_ask(question, knowledge_base, client, **kwargs):
        captured["chat_ask"] = {
            "question": question,
            "kb": knowledge_base,
            "client": client,
            "catalog": kwargs.get("sheet_catalog"),
        }
        return _FakeAnswer()

    class _FakeAnswer:
        def to_dict(self):
            return {
                "question": "q",
                "answer": "پاسخ واقعی مسیر RAG",
                "confidence": "needs_review",
                "sources": [{"file_key": "f", "sheet_names": [], "chunk_id": "c", "similarity": 0.5, "snippet": ""}],
                "route_reasoning": "دلیل",
            }

    monkeypatch.setattr(rag_ai_adapter, "build_readonly_knowledge_base", _fake_readonly)
    monkeypatch.setattr(rag_ai_adapter, "build_llm_client", _fake_llm_client)

    import llm_variable_resolver

    monkeypatch.setattr(llm_variable_resolver, "chat_ask", _fake_chat_ask)

    result = chatbot.ask_with_history(
        db_session,
        ready_project,
        "موجودی چقدر است؟",
        [{"role": "user", "content": "سلام"}, {"role": "assistant", "content": "سلام!"}],
    )

    # ۱) پایگاه‌دانش از همان جدیدترین نسخه‌ی آماده‌ی همین پروژه/کاربر ساخته شده است.
    assert captured["readonly"] == (
        ready_project.user_id,
        ready_project.id,
        ready_project.latest_ready_kb_id,
    )
    # ۲) کلاینت مدل با اسلاگ همین کارگاه ساخته شده است.
    assert captured["llm"] == (ready_project.id, SLUG)
    # ۳) شکل خروجی همان قرارداد مورد انتظار UI است.
    assert result["answer"] == "پاسخ واقعی مسیر RAG"
    assert result["confidence"] == "needs_review"
    assert result["route_reasoning"] == "دلیل"
    assert result["history_turns_used"] == 2
    assert result["sources"][0]["file_key"] == "f"
    # ۴) زمینه‌ی گفتگو در همان پرسشی که به مدل می‌رود گنجانده شده است.
    sent = captured["chat_ask"]
    assert sent["kb"] is read_only_kb
    assert sent["client"] is llm_client
    assert "گفتگوی قبلی" in sent["question"]
    assert "سلام!" in sent["question"]
    assert "موجودی چقدر است؟" in sent["question"]
    # ۵) کاتالوگ روتر از شیت‌های واقعی همان پایگاه‌دانش ساخته شده است.
    assert "financial_statements" in sent["catalog"]


def test_compose_question_is_unchanged_for_a_first_turn():
    composed = chatbot.compose_question("موجودی نقد چقدر است؟")
    assert "گفتگوی قبلی" not in composed
    assert composed.startswith("موجودی نقد چقدر است؟")


def test_compose_question_keeps_only_the_last_turns_and_caps_length():
    history = [
        {"role": "user", "content": f"پیام {index}"} for index in range(20)
    ]
    composed = chatbot.compose_question("سؤال تازه", history)

    assert chatbot.history_turns_used(history) == chatbot.MAX_CONTEXT_TURNS
    assert "پیام 19" in composed
    assert "پیام 0" not in composed
    # نوبت‌های ناشناخته/خالی بی‌سروصدا حذف می‌شوند.
    assert chatbot.history_turns_used([{"role": "system", "content": "x"}, {"role": "user", "content": ""}]) == 0


def test_ask_with_history_refuses_when_the_kb_has_no_searchable_text(
    db_session, ready_project, monkeypatch
):
    """پایگاه‌دانش آماده است اما متن قابل‌جست‌وجو ندارد (نسخه‌های پیش از این فاز)."""
    monkeypatch.setattr(
        rag_ai_adapter, "build_readonly_knowledge_base", lambda *args, **kwargs: None
    )

    with pytest.raises(chatbot.ChatbotUnavailableError) as excinfo:
        chatbot.ask_with_history(db_session, ready_project, "سؤال")

    assert str(excinfo.value) == chatbot.NO_INDEX_MESSAGE_FA


def test_is_available_follows_the_project_pointer(project, ready_project):
    assert chatbot.is_available(ready_project) is True
    assert chatbot.is_available(project) is False


# ---------------------------------------------------------------------------
# XSS / ذخیره‌نشدن محتوا (قابل‌سنجش در این لایه)
# ---------------------------------------------------------------------------
SUSPICIOUS_ANSWER = (
    '<script>alert("xss")</script>'
    '<img src=x onerror="alert(1)">'
    "[bad](javascript:alert(2))"
)


def test_model_output_is_transported_as_json_data_and_never_as_html(
    auth_client, ready_project, monkeypatch
):
    """پاسخ مدل هرگز به‌عنوان HTML سرو/تفسیر نمی‌شود و دست‌نخورده منتقل می‌شود.

    این تست عمداً «خروجی نهایی مرورگر» را ادعا نمی‌کند (``TestClient`` جاوااسکریپت
    اجرا نمی‌کند). چیزی که این‌جا تضمین می‌شود این است که سرور هیچ‌گاه پاسخ را به
    HTML تبدیل نمی‌کند: نوع محتوا ``application/json`` است، متن دقیقاً همان متن
    مدل برمی‌گردد (نه escape‌شده، نه «پاک‌سازی‌شده» -- تا رندر سمت مرورگر تنها
    نقطه‌ی تصمیم بماند)، و پاسخ در قالب هیچ‌گاه ``| safe`` نمی‌شود.
    """
    monkeypatch.setattr(
        "api.workshops.financial_chatbot.chatbot.ask_with_history",
        _fake_ask(answer=SUSPICIOUS_ANSWER, confidence="needs_review"),
    )
    chat = auth_client.post(_api(ready_project.id) + "/sessions", json={"title": "امنیت"}).json()

    response = auth_client.post(
        _api(ready_project.id) + f"/sessions/{chat['id']}/ask", json={"question": "سؤال"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    # متن خام مدل، بی‌کم‌وکاست، به‌عنوان *داده* برمی‌گردد.
    assert response.json()["answer"] == SUSPICIOUS_ANSWER


def test_front_end_sanitization_is_escape_first():
    """قرارداد امنیتی سمت مرورگر: فرار دادن متن، *پیش از* تبدیل Markdown.

    ``TestClient`` نمی‌تواند جاوااسکریپت اجرا کند، بنابراین به‌جای ادعای نادرست
    درباره‌ی DOM نهایی، همان قراردادی که این مصونیت را می‌سازد در منبع JS قفل
    می‌شود:

    ۱) ``escapeHtml`` وجود دارد و دقیقاً ``& < > " '`` را بی‌اثر می‌کند (شامل
       گیومه‌ها، چون URL پیوندها در ویژگی ``href`` قرار می‌گیرد).
    ۲) هر مسیر درون‌خطی Markdown به‌شکل ``renderInline(escapeHtml(...))`` نوشته
       شده است -- یعنی هیچ متنی بدون فرار به خروجی نمی‌رسد.
    ۳) محتوای پاسخ هم از همان مسیر می‌گذرد (``renderMarkdown(answer)``) و تنها
       منبع HTML عنصر پاسخ، همین تابع است.
    ۴) پیوندها فقط با پیشوندهای مجاز رندر می‌شوند (``javascript:`` رد می‌شود).
    ۵) قالب هیچ متنی را با ``| safe`` رندر نمی‌کند؛ تنها استثنا فراخوانی
       ``icon()`` است که SVG ثابت و درون‌برنامه‌ای برمی‌گرداند.
    """
    source = JS_PATH.read_text(encoding="utf-8")
    assert not source.startswith("\ufeff")

    # ۱) تابع فرار و پوشش کامل نویسه‌های خطرناک
    assert "function escapeHtml(value)" in source
    for replacement in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert replacement in source

    # ۲) هر بلوک درون‌خطی، متن را پیش از تبدیل فرار می‌دهد
    assert "renderInline(escapeHtml(" in source

    # ۳) پاسخ مدل فقط از مسیر امن به DOM می‌رود
    assert "renderMarkdown(answer)" in source
    assert "psa-chat-answer psa-prose" in source
    assert "innerHTML = answer" not in source
    assert "innerHTML = data.answer" not in source

    # ۴) پروتکل‌های ناشناخته‌ی پیوند (مثل javascript:) پذیرفته نمی‌شوند
    assert "function safeUrl(raw)" in source
    assert "/^https?:\\/\\//i" in source
    assert "/^mailto:/i" in source

    # ۵) قالب هرگز متنی را «امن» اعلام نمی‌کند
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    for line in template.splitlines():
        if "| safe" in line:
            assert "icon(" in line, f"مقدار غیرآیکون با | safe رندر شده است: {line.strip()}"
    # پیوندها و متن‌ها همیشه auto-escape می‌شوند (بدون هیچ فلتر bypass).
    assert "psa-chat-messages" in template


def test_history_and_messages_are_never_persisted_by_the_client():
    """قرارداد «بدون ذخیره‌سازی محتوا» در سمت مرورگر.

    تنها چیزی که در ``localStorage`` نوشته می‌شود شناسه‌ی آخرین گفتگوی باز است
    (متادیتا). آرایه‌ی حافظه‌ای پیام‌ها (``state.history``) هرگز سریالایز یا
    ذخیره نمی‌شود، و با انتخاب یک گفتگوی دیگر یا بارگذاری مجدد صفحه خالی می‌شود.
    """
    source = JS_PATH.read_text(encoding="utf-8")

    # ذخیره‌سازی مرورگر فقط شناسه‌ی گفتگو را می‌گیرد...
    assert "setItem(STORAGE_KEY, String(sessionId))" in source
    # ... و هیچ‌جا آرایه‌ی پیام‌ها سریالایز/ذخیره نمی‌شود.
    assert "JSON.stringify(state.history)" not in source
    assert "setItem(STORAGE_KEY, JSON" not in source
    # گفتگوی جاری همیشه با حافظه‌ی خالی شروع می‌شود (نه بازیابی پیام‌ها).
    assert "state.history = [];" in source

    # سمت سرور هم هیچ جدول پیامی وجود ندارد.
    from api.db import models

    assert not any("message" in name.lower() for name in models.Base.metadata.tables)
    assert "chat_sessions" in models.Base.metadata.tables
    assert list(models.Base.metadata.tables["chat_sessions"].columns.keys()) == [
        "id",
        "project_id",
        "user_id",
        "title",
        "created_at",
        "updated_at",
    ]


def test_workshop_is_registered_and_not_hidden_when_gated(auth_client, project):
    """کارگاه در رجیستری/ناوبری هست، حتی وقتی رابطش دروازه‌بندی شده است."""
    from api.workshops import WORKSHOPS

    assert SLUG in WORKSHOPS
    definition = WORKSHOPS[SLUG]
    assert definition.display_name_fa == "چت‌بات مالی"
    assert definition.settings_applied is True
    assert definition.has_download({}) is False
    assert definition.estimate_progress({}) == 0
    assert definition.summary_chips_fa({}) == []
    assert definition.collect_result({}).summary == {}

    dashboard = auth_client.get(f"/projects/{project.id}")
    assert f"/projects/{project.id}/{SLUG}" in dashboard.text
