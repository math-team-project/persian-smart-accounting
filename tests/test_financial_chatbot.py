"""تست‌های کارگاه «چت‌بات مالی» (گفتگوها، پیام‌های ماندگار، دروازه‌بندی، امنیت).

راهبرد تست، مثل بقیه‌ی کارگاه‌های این پروژه: هیچ LLM، embedder، Chroma store یا
شبکه‌ای درگیر نمی‌شود. در بیشتر تست‌ها ``financial_chatbot_service.ask_with_history``
با یک نسخه‌ی جعلی جایگزین می‌شود (یعنی مرز سرویس -- دقیقاً همان‌جایی که تست‌های
کارگاه‌های دیگر pipeline را جایگزین می‌کنند) و در یک تست جدا، خودِ سرویس واقعی با
``rag_ai_adapter`` جعلی اجرا می‌شود تا سیم‌کشی و شکل خروجی‌اش هم آزموده شود.

رفتار غیرهم‌زمان (۲۰۲ + polling) و تاریخچه‌ی ماندگار در فایل جداگانه‌ای آزموده
می‌شود: ``tests/test_financial_chatbot_history.py``. این فایل روی چیزهایی تمرکز
دارد که با آن تغییر عوض نشده‌اند -- جداسازی، دروازه‌بندی، شکل سرویس، و قرارداد
امنیتی رندر Markdown -- به‌علاوه‌ی چند قرارداد *جدید* در همان سطح (شکل ردیف پیام،
تاریخچه‌ای که فقط از سرور می‌آید، و حذف پیام‌ها همراه گفتگو).

سه چیز که این فایل به‌طور خاص تضمین می‌کند:

۱) **جداسازی**: یک گفتگو/پیام فقط با ``(project_id, user_id)`` خودش خوانده/حذف
   می‌شود؛ پروژه/کاربر دیگر آن را نمی‌بیند (۴۰۴، نه ۴۰۳).
۲) **دروازه‌بندی**: تا وقتی ``projects.latest_ready_kb_id`` خالی است، صفحه پیام
   فارسی راهنما نشان می‌دهد (نه رابط چت) و پرسش با پیام فارسی رد می‌شود.
۳) **تاریخچه‌ی کلاینت‌ساخت پذیرفته نمی‌شود**: زمینه‌ی چندنوبتی فقط از ردیف‌های
   ذخیره‌شده‌ی همان گفتگو ساخته می‌شود، پس هیچ کلاینتی نمی‌تواند یک «تاریخچه»ی
   جعلی به مدل تزریق کند.

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

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import WEB_DIR
from api.repositories import chat_messages as messages_repo
from api.repositories import chat_sessions as chat_repo
from api.repositories import knowledge_bases as kb_repo
from api.repositories import projects as projects_repo
from api.services import financial_chatbot_service as chatbot
from api.services import rag_ai_adapter

SLUG = "financial_chatbot"
JS_PATH = Path(WEB_DIR, "static", "js", "financial_chatbot.js")
TEMPLATE_PATH = Path(WEB_DIR, "templates", "financial_chatbot.html")


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


def _new_session(client: TestClient, project_id: int, title: str = "گفتگوی آزمون") -> dict:
    response = client.post(_api(project_id) + "/sessions", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()


def _wait_for_message(
    client: TestClient, project_id: int, session_id: str, message_id: str, timeout: float = 10.0
) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(_api(project_id) + f"/sessions/{session_id}/messages/{message_id}")
        assert response.status_code == 200, response.text
        row = response.json()
        if row["status"] in ("complete", "failed"):
            return row
        time.sleep(0.02)
    raise AssertionError(f"پیام {message_id} در مهلت مقرر نهایی نشد")


def _fake_ask(*, answer="پاسخ جعلی", confidence="high", sources=None, route_reasoning=""):
    """یک ``ask_with_history`` جعلی که استدلال‌هایش را هم ثبت می‌کند.

    امضای آن همان امضای واقعی سرویس است (``session, project, question,
    recent_history``) و تاریخچه را همان‌طور که سرویس می‌بیند ثبت می‌کند.
    """
    calls: list[dict] = []

    def _inner(session, project, question, recent_history=None):
        history = _as_dicts(recent_history)
        calls.append({"project_id": project.id, "question": question, "history": history})
        return {
            "answer": answer,
            "confidence": confidence,
            "sources": sources or [],
            "route_reasoning": route_reasoning,
            "history_turns_used": len(history),
        }

    _inner.calls = calls  # type: ignore[attr-defined]
    return _inner


def _as_dicts(rows) -> list[dict]:
    """نوبت‌های دریافتی سرویس را به دیکشنری ساده تبدیل می‌کند."""
    return [
        {"role": row.role, "content": row.content}
        if hasattr(row, "role")
        else {"role": row.get("role"), "content": row.get("content")}
        for row in (rows or [])
    ]


def _ask_and_wait(client: TestClient, project_id: int, session_id: str, question: str) -> dict:
    """پرسش می‌فرستد و تا نهایی‌شدن پاسخ صبر می‌کند (خروجی: ردیف پاسخ دستیار)."""
    response = client.post(
        _api(project_id) + f"/sessions/{session_id}/ask", json={"question": question}
    )
    assert response.status_code == 202, response.text
    started = response.json()
    return _wait_for_message(client, project_id, session_id, started["assistant_message_id"])


# ---------------------------------------------------------------------------
# گفتگوها: ساخت / فهرست / حذف
# ---------------------------------------------------------------------------
def test_create_session_returns_metadata_with_a_uuid_identifier(auth_client, project):
    response = auth_client.post(_api(project.id) + "/sessions", json={"title": "بررسی موجودی"})

    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "بررسی موجودی"
    # شناسه یک UUID رشته‌ای است (نه عدد خودکارافزاینده) -- همان الگوی پایگاه‌دانش،
    # تا کلید ترکیبی job پس‌زمینه واقعاً جهانی‌یکتا باشد.
    assert isinstance(data["id"], str)
    assert len(data["id"]) == 36 and data["id"].count("-") == 4
    assert data["created_at"] and data["updated_at"]
    # قرارداد «متادیتا»: هیچ کلید دیگری (مثل پیام‌ها) در پاسخ گفتگو نیست.
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
    first = _new_session(auth_client, project.id, title="اول")
    second = _new_session(auth_client, project.id, title="دوم")
    assert first["id"] != second["id"]

    listed = auth_client.get(_api(project.id) + "/sessions").json()["sessions"]
    assert [item["title"] for item in listed] == ["دوم", "اول"]


def test_ask_touches_the_session_so_it_moves_to_the_top(auth_client, ready_project, monkeypatch):
    first = _new_session(auth_client, ready_project.id, title="اول")
    second = _new_session(auth_client, ready_project.id, title="دوم")

    monkeypatch.setattr(
        "api.workshops.financial_chatbot.chatbot.ask_with_history",
        _fake_ask(answer="پاسخ آزمایشی"),
    )
    # گفتگوی قدیمی‌تر (اول) دوباره استفاده می‌شود.
    _ask_and_wait(auth_client, ready_project.id, first["id"], "سؤال")

    listed = auth_client.get(_api(ready_project.id) + "/sessions").json()["sessions"]
    assert listed[0]["id"] == first["id"]
    assert listed[1]["id"] == second["id"]


def test_delete_session_removes_only_that_row_and_its_messages(
    auth_client, ready_project, db_session, monkeypatch, user
):
    monkeypatch.setattr(
        "api.workshops.financial_chatbot.chatbot.ask_with_history", _fake_ask()
    )
    keep = _new_session(auth_client, ready_project.id, title="بماند")
    remove = _new_session(auth_client, ready_project.id, title="حذف")
    _ask_and_wait(auth_client, ready_project.id, remove["id"], "پرسش در گفتگوی حذف‌شونده")

    response = auth_client.delete(_api(ready_project.id) + f"/sessions/{remove['id']}")

    assert response.status_code == 204
    remaining = [
        item["id"]
        for item in auth_client.get(_api(ready_project.id) + "/sessions").json()["sessions"]
    ]
    assert remaining == [keep["id"]]
    # ردیف واقعاً از پایگاه‌داده رفته است (حذف سخت، نه علامت‌گذاری)...
    assert (
        chat_repo.get_by_id(
            db_session, remove["id"], user_id=ready_project.user_id, project_id=ready_project.id
        )
        is None
    )
    # ... و پیام‌هایش هم با آن رفته‌اند (هیچ ردیف یتیمی نمی‌ماند).
    assert messages_repo.list_by_session(
        db_session, remove["id"], project_id=ready_project.id, user_id=user.id
    ) == []
    assert messages_repo.list_by_session(
        db_session, keep["id"], project_id=ready_project.id, user_id=user.id
    ) == []


def test_delete_session_of_another_project_returns_404_and_keeps_the_row(
    auth_client, project, other_user_project, db_session, second_user
):
    chat = chat_repo.create(
        db_session, project_id=other_user_project.id, user_id=second_user.id, title="مال دیگری"
    )

    response = auth_client.delete(_api(project.id) + f"/sessions/{chat.id}")

    assert response.status_code == 404
    assert (
        chat_repo.get_by_id(
            db_session, chat.id, user_id=second_user.id, project_id=other_user_project.id
        )
        is not None
    )


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

    assert [c.id for c in chat_repo.list_by_project(db_session, project.id, user_id=user.id)] == [
        mine.id
    ]
    # همان پروژه اما کاربر دیگر: هیچ ردیفی برنمی‌گردد.
    assert chat_repo.list_by_project(db_session, project.id, user_id=second_user.id) == []
    assert (
        chat_repo.get_by_id(db_session, theirs.id, user_id=user.id, project_id=project.id) is None
    )
    # حذف با شناسه‌ی درست اما مالکیت نادرست، هیچ چیزی را پاک نمی‌کند.
    assert (
        chat_repo.delete_by_id(db_session, theirs.id, user_id=user.id, project_id=project.id)
        is False
    )
    assert (
        chat_repo.get_by_id(
            db_session, theirs.id, user_id=second_user.id, project_id=other_project.id
        )
        is not None
    )


def test_deleting_a_project_also_removes_its_chat_sessions_and_messages(
    db_session, project, user
):
    """حذف پروژه نباید ردیف یتیم گفتگو یا پیام باقی بگذارد.

    این تست مسیر cascade را در سطح ORM/SQLite قفل می‌کند؛ مسیر واقعی حذف پروژه
    (که همین دو جدول را صریح و با گزارش تعداد پاک می‌کند) در
    ``tests/test_projects.py`` آزموده می‌شود.
    """
    chat = chat_repo.create(db_session, project_id=project.id, user_id=user.id, title="یتیم‌نشو")
    messages_repo.create_user_message(
        db_session,
        session_id=chat.id,
        project_id=project.id,
        user_id=user.id,
        content="یک پرسش",
    )
    projects_repo.delete(db_session, project)

    assert db_session.query(chat_repo.ChatSession).all() == []
    assert db_session.query(messages_repo.ChatMessage).all() == []


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
    chat = _new_session(auth_client, project.id, title="خاموش")

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
        _api(ready_project.id) + "/sessions/00000000-0000-0000-0000-000000000000/ask",
        json={"question": "سؤال"},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# پرسش: مسیر API با سرویس جعلی (بدون LLM/embedder/Chroma)
# ---------------------------------------------------------------------------
def test_a_saved_answer_keeps_its_markdown_text_sources_and_confidence(
    auth_client, ready_project, monkeypatch
):
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
    chat = _new_session(auth_client, ready_project.id, title="شکل")

    row = _ask_and_wait(auth_client, ready_project.id, chat["id"], "موجودی نقد چقدر است؟")

    assert row["status"] == "complete"
    assert row["confidence"] == "medium"
    assert row["route_reasoning"] == "شیت مرتبط پیدا شد"
    assert row["sources"] == [
        {
            "file_key": "financial_statements",
            "sheet_names": ["ترازنامه"],
            "chunk_id": "c1",
            # منابع عیناً همان دیکشنری سرویس‌اند (گردکردن شباهت کار خود پکیج RAG،
            # در ``ChatSource.to_dict``، است نه این لایه).
            "similarity": 0.8123,
            "snippet": "موجودی نقد | 100",
        }
    ]
    # متن مدل دست‌نخورده ذخیره/برگردانده می‌شود (هیچ escape سمت سرور).
    assert row["content"] == "**موجودی نقد** برابر ۱۰۰ است."
    assert fake.calls[0]["question"] == "موجودی نقد چقدر است؟"


def test_a_client_supplied_history_is_ignored(auth_client, ready_project, monkeypatch):
    """زمینه‌ی گفتگو فقط از ردیف‌های ذخیره‌شده می‌آید؛ payload کلاینت بی‌اثر است.

    پیش از این فاز، مرورگر خودش ``recent_history`` را می‌فرستاد. با تاریخچه‌ی
    سرور-ساید، یک کلاینت دست‌کاری‌شده نباید بتواند زمینه‌ی جعلی به مدل تزریق کند.
    """
    fake = _fake_ask()
    monkeypatch.setattr("api.workshops.financial_chatbot.chatbot.ask_with_history", fake)
    chat = _new_session(auth_client, ready_project.id, title="زمینه")

    response = auth_client.post(
        _api(ready_project.id) + f"/sessions/{chat['id']}/ask",
        json={
            "question": "پرسش تازه",
            "recent_history": [{"role": "user", "content": "تاریخچه‌ی جعلی"}],
        },
    )
    assert response.status_code == 202, response.text
    _wait_for_message(
        auth_client, ready_project.id, chat["id"], response.json()["assistant_message_id"]
    )

    assert fake.calls[0]["question"] == "پرسش تازه"
    assert fake.calls[0]["history"] == []


def test_ask_rejects_an_empty_question(auth_client, ready_project, monkeypatch):
    monkeypatch.setattr(
        "api.workshops.financial_chatbot.chatbot.ask_with_history",
        _fake_ask(),
    )
    chat = _new_session(auth_client, ready_project.id, title="خالی")

    response = auth_client.post(
        _api(ready_project.id) + f"/sessions/{chat['id']}/ask", json={"question": ""}
    )

    # سقف/کف اعتبارسنجی Pydantic پیش از رسیدن به سرویس جلوی پرسش خالی را می‌گیرد.
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# زمینه‌ی گفتگو: ساخت از ردیف‌های ذخیره‌شده
# ---------------------------------------------------------------------------
def test_build_recent_history_excludes_the_given_rows_and_caps_the_window(
    db_session, ready_project, user
):
    chat = chat_repo.create(
        db_session, project_id=ready_project.id, user_id=user.id, title="زمینه"
    )
    rows = [
        messages_repo.create_user_message(
            db_session,
            session_id=chat.id,
            project_id=ready_project.id,
            user_id=user.id,
            content=f"پیام {index}",
        )
        for index in range(10)
    ]

    history = chatbot.build_recent_history(
        db_session,
        session_id=chat.id,
        project_id=ready_project.id,
        user_id=user.id,
        exclude_message_ids=(rows[-1].id,),
    )

    assert len(history) == chatbot.MAX_CONTEXT_TURNS
    assert history[-1]["content"] == "پیام 8"
    assert all(turn["content"] != "پیام 9" for turn in history)
    # ردیف‌های بی‌متن (پاسخ ``pending``) هرگز به مدل داده نمی‌شوند.
    messages_repo.create_pending_assistant_message(
        db_session, session_id=chat.id, project_id=ready_project.id, user_id=user.id
    )
    assert all(turn["content"].strip() for turn in chatbot.build_recent_history(
        db_session,
        session_id=chat.id,
        project_id=ready_project.id,
        user_id=user.id,
    ))


def test_build_recent_history_never_reads_another_session_or_user(db_session, ready_project, user, second_user):
    other_project = projects_repo.create(db_session, second_user.id, "پروژه‌ی دیگر")
    mine = chat_repo.create(
        db_session, project_id=ready_project.id, user_id=user.id, title="مال من"
    )
    other_project_chat = chat_repo.create(
        db_session, project_id=other_project.id, user_id=second_user.id, title="مال دیگری"
    )
    messages_repo.create_user_message(
        db_session,
        session_id=other_project_chat.id,
        project_id=other_project.id,
        user_id=second_user.id,
        content="محرمانه",
    )

    assert (
        chatbot.build_recent_history(
            db_session,
            session_id=mine.id,
            project_id=ready_project.id,
            user_id=user.id,
        )
        == []
    )
    # کلید نادرست (همان گفتگو، پروژه‌ی دیگر): باز هم چیزی خوانده نمی‌شود.
    assert (
        chatbot.build_recent_history(
            db_session,
            session_id=other_project_chat.id,
            project_id=ready_project.id,
            user_id=user.id,
        )
        == []
    )


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
    # ۶) خودِ این تابع چیزی نمی‌نویسد (ثبت نتیجه کار لایه‌ی سرویس/پس‌زمینه است).
    assert db_session.query(messages_repo.ChatMessage).count() == 0


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
# پاک‌سازی راه‌اندازی: پیام‌های ناتمام فرآیند قبلی
# ---------------------------------------------------------------------------
def test_pending_messages_of_a_previous_process_are_marked_failed(db_session, ready_project, user):
    chat = chat_repo.create(
        db_session, project_id=ready_project.id, user_id=user.id, title="ناتمام"
    )
    stuck = messages_repo.create_pending_assistant_message(
        db_session, session_id=chat.id, project_id=ready_project.id, user_id=user.id
    )

    marked = messages_repo.mark_pending_messages_failed(db_session, error_message="سرور ری‌استارت شد.")

    assert marked == 1
    db_session.expire_all()
    refreshed = db_session.get(messages_repo.ChatMessage, stuck.id)
    assert refreshed.status == "failed"
    assert refreshed.error_message == "سرور ری‌استارت شد."


# ---------------------------------------------------------------------------
# XSS / انتقال امن محتوای مدل (قابل‌سنجش در این لایه)
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
    chat = _new_session(auth_client, ready_project.id, title="امنیت")

    response = auth_client.get(
        _api(ready_project.id) + f"/sessions/{chat['id']}/messages"
    )
    assert response.headers["content-type"].startswith("application/json")

    row = _ask_and_wait(auth_client, ready_project.id, chat["id"], "سؤال")
    assert row["content"] == SUSPICIOUS_ANSWER

    listed = auth_client.get(_api(ready_project.id) + f"/sessions/{chat['id']}/messages")
    assert listed.headers["content-type"].startswith("application/json")
    # متن خام مدل، بی‌کم‌وکاست، به‌عنوان *داده* برمی‌گردد.
    assert listed.json()["messages"][-1]["content"] == SUSPICIOUS_ANSWER


def test_front_end_sanitization_is_escape_first():
    """قرارداد امنیتی سمت مرورگر: فرار دادن متن، *پیش از* تبدیل Markdown.

    ``TestClient`` نمی‌تواند جاوااسکریپت اجرا کند، بنابراین به‌جای ادعای نادرست
    درباره‌ی DOM نهایی، همان قراردادی که این مصونیت را می‌سازد در منبع JS قفل
    می‌شود:

    ۱) ``escapeHtml`` وجود دارد و دقیقاً ``& < > " '`` را بی‌اثر می‌کند (شامل
       گیومه‌ها، چون URL پیوندها در ویژگی ``href`` قرار می‌گیرد).
    ۲) هر مسیر درون‌خطی Markdown به‌شکل ``renderInline(escapeHtml(...))`` نوشته
       شده است -- یعنی هیچ متنی بدون فرار به خروجی نمی‌رسد.
    ۳) محتوای پاسخ هم از همان مسیر می‌گذرد (``renderMarkdown(message.content``)
       و تنها منبع HTML عنصر پاسخ، همین تابع است.
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
    assert "renderMarkdown(message.content" in source
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


def test_the_browser_stores_only_the_open_session_id_and_loads_messages_from_the_server():
    """قرارداد تاریخچه‌ی سرور-ساید در سمت مرورگر.

    تنها چیزی که در ``localStorage`` نوشته می‌شود شناسه‌ی آخرین گفتگوی باز است
    (متادیتا). پیام‌ها در مرورگر ذخیره نمی‌شوند؛ هر بار از سرور خوانده می‌شوند
    (``GET .../messages``) و زمینه‌ی چندنوبتی هم سمت سرور از همان ردیف‌ها ساخته
    می‌شود.
    """
    source = JS_PATH.read_text(encoding="utf-8")

    # ذخیره‌سازی مرورگر فقط شناسه‌ی گفتگو را می‌گیرد...
    assert "setItem(STORAGE_KEY, String(sessionId))" in source
    assert "setItem(STORAGE_KEY, JSON" not in source
    # ... و هیچ آرایه‌ی محلی‌ای از پیام‌ها وجود ندارد که تاریخچه را جعل کند.
    assert "state.history" not in source
    assert "recent_history" not in source

    # سمت سرور: جدول پیام‌ها با همه‌ی ستون‌های مستندش وجود دارد.
    from api.db import models

    table = models.Base.metadata.tables["chat_messages"]
    assert list(table.columns.keys()) == [
        "id",
        "session_id",
        "project_id",
        "user_id",
        "role",
        "content",
        "status",
        "confidence",
        "sources",
        "route_reasoning",
        "error_message",
        "created_at",
    ]
    # شناسه‌ی گفتگو هم (مثل پایگاه‌دانش) یک UUID رشته‌ای است.
    assert str(models.Base.metadata.tables["chat_sessions"].columns["id"].type) == "VARCHAR(36)"


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
