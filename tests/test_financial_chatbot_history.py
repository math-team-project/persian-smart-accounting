"""تست‌های «تاریخچه‌ی ماندگار + پرسش پس‌زمینه»ی کارگاه چت‌بات مالی.

این فایل همان قراردادهایی را قفل می‌کند که این فاز اضافه کرده است:

۱) **ثبت آنی، پاسخ در پس‌زمینه**: ``POST .../ask`` بلافاصله (۲۰۲) با شناسه‌ی دو
   پیام برمی‌گردد -- حتی وقتی تابع پاسخ‌دهی عمداً مسدود شده باشد. پس هیچ درخواست
   HTTP ای منتظر مدل نمی‌ماند (تست با یک ``threading.Event`` قطعی سنجیده می‌شود،
   نه با اندازه‌گیری زمان).
۲) **تاریخچه‌ی ماندگار**: باز کردن دوباره‌ی یک گفتگو، همان پیام‌های ذخیره‌شده را
   به ترتیب برمی‌گرداند -- شامل پرسشی که هنوز پاسخش در حال تولید است.
۳) **polling**: وضعیت یک پیام از ``pending`` به ``complete`` (یا ``failed``) می‌رسد.
۴) **بدون قاطی‌شدن**: دو پرسش هم‌زمان در دو گفتگو هرکدام پاسخ خودشان را روی ردیف
   خودشان می‌گیرند؛ کلید job پس‌زمینه سه‌گانه‌ی
   ``(project_id, session_id, assistant_message_id)`` است.
۵) **جداسازی**: هیچ پیامی از مرز پروژه/کاربر/گفتگو عبور نمی‌کند (۴۰۴، نه ۴۰۳).
۶) **باگ نمایش پیام کاربر**: پرسش کاربر همان لحظه در پایگاه‌داده و در پاسخ API
   هست، و سمت مرورگر هم پیش از رفتن درخواست رندر می‌شود (بخش دوم با یک بررسی
   منبع JS سنجیده می‌شود، چون ``TestClient`` جاوااسکریپت اجرا نمی‌کند).

هیچ LLM/embedder/Chroma واقعی درگیر نمی‌شود: ``financial_chatbot_service.
ask_with_history`` (مرز سرویس) جعلی می‌شود و در یک تست، خودِ سرویس واقعی با
``rag_ai_adapter`` جعلی اجرا می‌شود.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from api.db.base import SessionLocal
from api.db.models import ChatMessage
from api.jobs.chat_jobs import chat_ask_jobs
from api.main import WEB_DIR, app
from api.repositories import chat_messages as messages_repo
from api.repositories import chat_sessions as sessions_repo
from api.repositories import knowledge_bases as kb_repo
from api.repositories import projects as projects_repo
from api.services import financial_chatbot_service as chatbot

SLUG = "financial_chatbot"
JS_PATH = Path(WEB_DIR, "static", "js", "financial_chatbot.js")

USERNAME = "tester"
PASSWORD = "secret123"


def _api(project_id: int) -> str:
    return f"/api/projects/{project_id}/{SLUG}"


def _make_ready_kb(db_session, *, project_id: int, user_id: int) -> str:
    """یک پایگاه‌دانش ``ready`` می‌سازد و اشاره‌گر پروژه را به آن می‌بندد."""
    kb = kb_repo.create(
        db_session,
        project_id=project_id,
        user_id=user_id,
        embedding_model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        embedding_device="cpu",
        chroma_collection_name="kb-chat-history-test",
        chroma_persist_dir="1/1/chat-history-test",
    )
    kb_repo.mark_ready_and_update_project_pointer(db_session, kb.id)
    return kb.id


@pytest.fixture()
def ready_project(db_session, user):
    """پروژه‌ای با پایگاه‌دانش آماده (کارگاه چک‌لیست قبلاً اجرا شده است)."""
    project = projects_repo.create(db_session, user.id, "پروژهٔ دارای پایگاه‌دانش")
    _make_ready_kb(db_session, project_id=project.id, user_id=user.id)
    db_session.expire_all()
    return project


def _new_session(client: TestClient, project_id: int, title: str = "گفتگوی آزمون") -> str:
    response = client.post(_api(project_id) + "/sessions", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _ask(client: TestClient, project_id: int, session_id: str, question: str) -> dict:
    response = client.post(
        _api(project_id) + f"/sessions/{session_id}/ask", json={"question": question}
    )
    assert response.status_code == 202, response.text
    return response.json()


def _get_message(client: TestClient, project_id: int, session_id: str, message_id: str) -> dict:
    response = client.get(
        _api(project_id) + f"/sessions/{session_id}/messages/{message_id}"
    )
    assert response.status_code == 200, response.text
    return response.json()


def _wait_for_message(
    client: TestClient,
    project_id: int,
    session_id: str,
    message_id: str,
    timeout: float = 10.0,
) -> dict:
    """تا رسیدن یک پیام به وضعیت پایانی صبر می‌کند و همان ردیف را برمی‌گرداند."""
    deadline = time.time() + timeout
    last: Optional[dict] = None
    while time.time() < deadline:
        last = _get_message(client, project_id, session_id, message_id)
        if last["status"] in ("complete", "failed"):
            return last
        time.sleep(0.02)
    raise AssertionError(f"پیام {message_id} در مهلت مقرر نهایی نشد؛ آخرین وضعیت: {last}")


def _blocking_ask(*, answer="پاسخ آزمایشی", confidence="high", sources=None, error=None, release=None):
    """``ask_with_history`` جعلی که تا باز شدن ``release`` مسدود می‌ماند.

    این «قفل» به تست اجازه می‌دهد *قطعاً* ثابت کند پاسخ HTTP منتظر تابع پاسخ‌دهی
    نمانده است: در لحظه‌ی دریافت پاسخ، این تابع هنوز در حال اجراست.
    """
    calls: list[dict] = []

    def _inner(session, project, question, recent_history=None):
        calls.append(
            {
                "project_id": project.id,
                "question": question,
                "history": [
                    {"role": turn.role, "content": turn.content}
                    if hasattr(turn, "role")
                    else dict(turn)
                    for turn in (recent_history or [])
                ],
            }
        )
        if release is not None:
            release.wait(timeout=10)
        if error is not None:
            raise error
        return {
            "answer": answer,
            "confidence": confidence,
            "sources": sources or [],
            "route_reasoning": "دلیل آزمایشی",
            "history_turns_used": len(calls[-1]["history"]),
        }

    _inner.calls = calls  # type: ignore[attr-defined]
    return _inner


# ---------------------------------------------------------------------------
# ۱) ثبت آنی + پاسخ پس‌زمینه
# ---------------------------------------------------------------------------
def test_ask_returns_a_202_immediately_while_the_answer_is_still_being_built(
    auth_client, ready_project, monkeypatch
):
    release = threading.Event()
    fake = _blocking_ask(answer="پاسخ نهایی", release=release)
    monkeypatch.setattr(chatbot, "ask_with_history", fake)
    session_id = _new_session(auth_client, ready_project.id)

    started = _ask(auth_client, ready_project.id, session_id, "موجودی نقد چقدر است؟")

    # پاسخ HTTP رسیده است، در حالی که تابع پاسخ‌دهی *هنوز* مسدود است.
    assert release.is_set() is False
    assert started["status"] == "pending"
    assert started["user_message_id"] and started["assistant_message_id"]
    assert set(started) == {"user_message_id", "assistant_message_id", "status"}

    # ردیف پرسش کاربر همین حالا ذخیره شده و از تاریخچه قابل خواندن است.
    user_row = _get_message(
        auth_client, ready_project.id, session_id, started["user_message_id"]
    )
    assert user_row["role"] == "user"
    assert user_row["status"] == "complete"
    assert user_row["content"] == "موجودی نقد چقدر است؟"

    # ردیف پاسخ هنوز ``pending`` و خالی است -- همان چیزی که UI جای آن اسپینر می‌گذارد.
    pending_row = _get_message(
        auth_client, ready_project.id, session_id, started["assistant_message_id"]
    )
    assert pending_row["role"] == "assistant"
    assert pending_row["status"] == "pending"
    assert pending_row["content"] == ""
    assert pending_row["sources"] == []

    release.set()
    final = _wait_for_message(
        auth_client, ready_project.id, session_id, started["assistant_message_id"]
    )
    assert final["status"] == "complete"
    assert final["content"] == "پاسخ نهایی"
    assert final["confidence"] == "high"
    assert fake.calls[0]["question"] == "موجودی نقد چقدر است؟"


def test_ask_rejects_a_blank_question_without_persisting_anything(
    auth_client, ready_project, monkeypatch
):
    monkeypatch.setattr(chatbot, "ask_with_history", _blocking_ask())
    session_id = _new_session(auth_client, ready_project.id)

    response = auth_client.post(
        _api(ready_project.id) + f"/sessions/{session_id}/ask", json={"question": "   "}
    )

    assert response.status_code == 422
    assert response.json()["detail"] == chatbot.EMPTY_QUESTION_FA
    listed = auth_client.get(_api(ready_project.id) + f"/sessions/{session_id}/messages")
    assert listed.json() == {"messages": []}


def test_ask_is_still_refused_with_the_gate_message_and_writes_no_rows(
    auth_client, project, monkeypatch
):
    """بدون پایگاه‌دانش آماده، پرسش همان ۴۰۹ قبلی را می‌دهد و هیچ پیامی ذخیره نمی‌شود."""
    monkeypatch.setattr(chatbot, "ask_with_history", _blocking_ask())
    session_id = _new_session(auth_client, project.id)

    response = auth_client.post(
        _api(project.id) + f"/sessions/{session_id}/ask", json={"question": "چقدر بود؟"}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == chatbot.GATED_MESSAGE_FA
    assert auth_client.get(_api(project.id) + f"/sessions/{session_id}/messages").json() == {
        "messages": []
    }


# ---------------------------------------------------------------------------
# ۲) polling: pending → complete / failed
# ---------------------------------------------------------------------------
def test_polling_endpoint_moves_from_pending_to_complete(auth_client, ready_project, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(chatbot, "ask_with_history", _blocking_ask(release=release))
    session_id = _new_session(auth_client, ready_project.id)
    started = _ask(auth_client, ready_project.id, session_id, "سؤال")

    first = _get_message(auth_client, ready_project.id, session_id, started["assistant_message_id"])
    assert first["status"] == "pending"

    release.set()
    final = _wait_for_message(
        auth_client, ready_project.id, session_id, started["assistant_message_id"]
    )
    assert final["status"] == "complete"


def test_polling_endpoint_reports_a_failure_with_a_persian_message(
    auth_client, ready_project, monkeypatch
):
    monkeypatch.setattr(
        chatbot,
        "ask_with_history",
        _blocking_ask(error=chatbot.ChatbotUnavailableError(chatbot.NO_INDEX_MESSAGE_FA)),
    )
    session_id = _new_session(auth_client, ready_project.id)
    started = _ask(auth_client, ready_project.id, session_id, "سؤال")

    final = _wait_for_message(
        auth_client, ready_project.id, session_id, started["assistant_message_id"]
    )

    assert final["status"] == "failed"
    assert final["error_message"] == chatbot.NO_INDEX_MESSAGE_FA


def test_unexpected_errors_never_leak_a_raw_message_to_the_user(
    auth_client, ready_project, monkeypatch
):
    monkeypatch.setattr(
        chatbot, "ask_with_history", _blocking_ask(error=RuntimeError("boom: internal detail"))
    )
    session_id = _new_session(auth_client, ready_project.id)
    started = _ask(auth_client, ready_project.id, session_id, "سؤال")

    final = _wait_for_message(
        auth_client, ready_project.id, session_id, started["assistant_message_id"]
    )

    assert final["status"] == "failed"
    # پیام فنی خام هرگز به کاربر نمی‌رسد؛ همان پیام فارسی عمومی ثبت می‌شود.
    assert final["error_message"] == chatbot.ASK_FAILED_FA
    assert "boom" not in (final["error_message"] or "")


def test_a_completed_answer_also_moves_its_session_to_the_top(
    auth_client, ready_project, monkeypatch
):
    """«آخرین استفاده» فقط پس از پاسخ موفق تازه می‌شود."""
    first = _new_session(auth_client, ready_project.id, title="اول")
    second = _new_session(auth_client, ready_project.id, title="دوم")
    monkeypatch.setattr(chatbot, "ask_with_history", _blocking_ask(answer="پاسخ"))
    started = _ask(auth_client, ready_project.id, first, "سؤال")
    _wait_for_message(auth_client, ready_project.id, first, started["assistant_message_id"])

    listed = auth_client.get(_api(ready_project.id) + "/sessions").json()["sessions"]
    assert listed[0]["id"] == first
    assert listed[1]["id"] == second


# ---------------------------------------------------------------------------
# ۳) تاریخچه‌ی ماندگار: باز کردن دوباره‌ی گفتگو
# ---------------------------------------------------------------------------
def test_reopening_a_session_returns_every_persisted_message_in_order(
    auth_client, ready_project, monkeypatch
):
    monkeypatch.setattr(
        chatbot,
        "ask_with_history",
        lambda session, project, question, recent_history=None: {
            "answer": f"پاسخ به {question}",
            "confidence": "medium",
            "sources": [],
            "route_reasoning": "",
            "history_turns_used": 0,
        },
    )
    session_id = _new_session(auth_client, ready_project.id, title="تاریخچه")

    for question in ("پرسش اول", "پرسش دوم"):
        started = _ask(auth_client, ready_project.id, session_id, question)
        _wait_for_message(auth_client, ready_project.id, session_id, started["assistant_message_id"])

    # «باز کردن دوباره‌ی» گفتگو: دقیقاً همان درخواستی که UI می‌فرستد.
    listed = auth_client.get(_api(ready_project.id) + f"/sessions/{session_id}/messages").json()

    assert [(row["role"], row["content"]) for row in listed["messages"]] == [
        ("user", "پرسش اول"),
        ("assistant", "پاسخ به پرسش اول"),
        ("user", "پرسش دوم"),
        ("assistant", "پاسخ به پرسش دوم"),
    ]
    # ترتیب صعودی زمانی است (قدیمی‌ترین اول) -- همان ترتیبی که UI رندر می‌کند.
    timestamps = [row["created_at"] for row in listed["messages"]]
    assert timestamps == sorted(timestamps)
    # شکل هر ردیف همان قرارداد UI است.
    assert set(listed["messages"][0]) == {
        "id",
        "session_id",
        "role",
        "content",
        "status",
        "confidence",
        "sources",
        "route_reasoning",
        "error_message",
        "created_at",
    }


def test_history_load_includes_a_still_pending_answer_and_persists_turns_as_context(
    auth_client, ready_project, monkeypatch
):
    """کاربر می‌رود و برمی‌گردد: پاسخ ناتمام دوباره ``pending`` دیده می‌شود.

    همچنین بررسی می‌شود که زمینه‌ی چندنوبتی از *ردیف‌های ذخیره‌شده* ساخته می‌شود
    (نه از چیزی که کلاینت فرستاده): پرسش دوم باید پرسش/پاسخ اول را در پرامپت مدل
    ببیند.
    """
    # پرسش اول آزاد است؛ پرسش دوم تا پایان تست مسدود می‌ماند.
    releases = [threading.Event(), threading.Event()]
    releases[0].set()
    calls: list[dict] = []

    def _inner(session, project, question, recent_history=None):
        calls.append(
            {
                "question": question,
                "history": [
                    {"role": turn.role, "content": turn.content}
                    if hasattr(turn, "role")
                    else dict(turn)
                    for turn in (recent_history or [])
                ],
            }
        )
        releases[min(len(calls) - 1, len(releases) - 1)].wait(timeout=10)
        return {
            "answer": f"پاسخ به {question}",
            "confidence": "high",
            "sources": [],
            "route_reasoning": "",
            "history_turns_used": len(calls[-1]["history"]),
        }

    monkeypatch.setattr(chatbot, "ask_with_history", _inner)
    session_id = _new_session(auth_client, ready_project.id)

    first = _ask(auth_client, ready_project.id, session_id, "پرسش اول")
    _wait_for_message(auth_client, ready_project.id, session_id, first["assistant_message_id"])

    second = _ask(auth_client, ready_project.id, session_id, "پرسش دوم")

    reopened = auth_client.get(_api(ready_project.id) + f"/sessions/{session_id}/messages").json()
    assert [(row["role"], row["status"]) for row in reopened["messages"]] == [
        ("user", "complete"),
        ("assistant", "complete"),
        ("user", "complete"),
        ("assistant", "pending"),
    ]
    assert reopened["messages"][-1]["id"] == second["assistant_message_id"]

    releases[1].set()
    _wait_for_message(auth_client, ready_project.id, session_id, second["assistant_message_id"])

    # زمینه‌ی پرسش دوم از پیام‌های ذخیره‌شده‌ی همان گفتگو ساخته شده است.
    assert calls[0]["question"] == "پرسش اول"
    assert calls[1]["question"] == "پرسش دوم"
    assert calls[0]["history"] == []
    assert {"role": "user", "content": "پرسش اول"} in calls[1]["history"]
    assert {"role": "assistant", "content": "پاسخ به پرسش اول"} in calls[1]["history"]


def test_context_never_leaks_across_sessions_even_for_the_same_project(
    auth_client, ready_project, monkeypatch
):
    """گفتگوی دیگر همان پروژه هرگز زمینه‌ی این گفتگو را نمی‌بیند."""
    fake = _blocking_ask(answer="پاسخ")
    monkeypatch.setattr(chatbot, "ask_with_history", fake)
    session_a = _new_session(auth_client, ready_project.id, title="الف")
    session_b = _new_session(auth_client, ready_project.id, title="ب")

    first = _ask(auth_client, ready_project.id, session_a, "متن مخصوص گفتگوی الف")
    _wait_for_message(auth_client, ready_project.id, session_a, first["assistant_message_id"])
    second = _ask(auth_client, ready_project.id, session_b, "متن مخصوص گفتگوی ب")
    _wait_for_message(auth_client, ready_project.id, session_b, second["assistant_message_id"])

    assert [call["history"] for call in fake.calls] == [[], []]


# ---------------------------------------------------------------------------
# ۴) هم‌زمانی: کلید سه‌گانه‌ی job و نبود قاطی‌شدن
# ---------------------------------------------------------------------------
def test_the_background_job_key_is_the_full_project_session_message_triple(
    auth_client, ready_project, monkeypatch
):
    release = threading.Event()
    monkeypatch.setattr(chatbot, "ask_with_history", _blocking_ask(release=release))
    session_id = _new_session(auth_client, ready_project.id)
    started = _ask(auth_client, ready_project.id, session_id, "سؤال")

    key = (ready_project.id, session_id, started["assistant_message_id"])
    assert chat_ask_jobs.is_running(key) is True
    # هیچ جست‌وجویی با یک شناسه‌ی تنها یا با کلید ناقص، این job را پیدا نمی‌کند.
    assert chat_ask_jobs.get((ready_project.id + 999, session_id, started["assistant_message_id"])) is None
    assert chat_ask_jobs.get((ready_project.id, "session-other", started["assistant_message_id"])) is None
    assert chat_ask_jobs.get((ready_project.id, session_id, "message-other")) is None

    release.set()
    _wait_for_message(auth_client, ready_project.id, session_id, started["assistant_message_id"])
    assert chat_ask_jobs.is_running(key) is False
    assert chat_ask_jobs.get(key)["status"] == "done"


def test_two_concurrent_questions_in_two_sessions_each_land_on_their_own_row(
    auth_client, ready_project, monkeypatch
):
    """دو پرسش هم‌زمان، دو گفتگو: هر پاسخ روی ردیف خودش می‌نشیند (بدون قاطی‌شدن).

    ``Barrier`` تضمین می‌کند این دو واقعاً هم‌زمان اجرا شوند (نه پشت‌سرهم): اگر
    یکی کامل می‌شد و بعد دیگری شروع می‌شد، سد با تایم‌اوت می‌شکست. یعنی این تست
    «اجرای هم‌زمان، بدون صف‌شدن پشت یکدیگر» را هم می‌سنجد.
    """
    barrier = threading.Barrier(2, timeout=5)
    broken = {"value": False}

    def _inner(session, project, question, recent_history=None):
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            broken["value"] = True
        return {
            "answer": f"پاسخ مخصوص «{question}»",
            "confidence": "high",
            "sources": [],
            "route_reasoning": "",
            "history_turns_used": 0,
        }

    monkeypatch.setattr(chatbot, "ask_with_history", _inner)
    session_x = _new_session(auth_client, ready_project.id, title="ایکس")
    session_y = _new_session(auth_client, ready_project.id, title="وای")

    question_x = "پرسش مربوط به گفتگوی ایکس"
    question_y = "پرسش مربوط به گفتگوی وای"
    started_x = _ask(auth_client, ready_project.id, session_x, question_x)
    started_y = _ask(auth_client, ready_project.id, session_y, question_y)

    final_x = _wait_for_message(auth_client, ready_project.id, session_x, started_x["assistant_message_id"])
    final_y = _wait_for_message(auth_client, ready_project.id, session_y, started_y["assistant_message_id"])

    assert broken["value"] is False, "دو پرسش هم‌زمان واقعاً موازی اجرا نشدند"
    assert final_x["content"] == f"پاسخ مخصوص «{question_x}»"
    assert final_y["content"] == f"پاسخ مخصوص «{question_y}»"

    history_x = auth_client.get(_api(ready_project.id) + f"/sessions/{session_x}/messages").json()
    history_y = auth_client.get(_api(ready_project.id) + f"/sessions/{session_y}/messages").json()
    assert [row["content"] for row in history_x["messages"]] == [question_x, final_x["content"]]
    assert [row["content"] for row in history_y["messages"]] == [question_y, final_y["content"]]


# ---------------------------------------------------------------------------
# ۵) جداسازی پیام‌ها (سه‌دامنه‌ای)
# ---------------------------------------------------------------------------
def test_a_message_cannot_be_polled_through_another_session_or_project(
    auth_client, ready_project, db_session, project, user
):
    session_a = _new_session(auth_client, ready_project.id, title="الف")
    session_b = _new_session(auth_client, ready_project.id, title="ب")
    row = messages_repo.create_user_message(
        db_session,
        session_id=session_a,
        project_id=ready_project.id,
        user_id=user.id,
        content="پیام محرمانه",
    )

    # همان پروژه/کاربر، اما گفتگویی دیگر: پیام دیده نمی‌شود.
    response = auth_client.get(
        _api(ready_project.id) + f"/sessions/{session_b}/messages/{row.id}"
    )
    assert response.status_code == 404
    # ... و در فهرست پیام‌های آن گفتگوی دیگر هم نمی‌آید.
    listed = auth_client.get(_api(ready_project.id) + f"/sessions/{session_b}/messages").json()
    assert listed == {"messages": []}

    # پروژه‌ی دیگر همان کاربر (حتی با همان شناسه‌ی گفتگو در مسیر): ۴۰۴.
    response = auth_client.get(_api(project.id) + f"/sessions/{session_a}/messages/{row.id}")
    assert response.status_code == 404
    response = auth_client.get(_api(project.id) + f"/sessions/{session_a}/messages")
    assert response.status_code == 404


def test_messages_of_another_users_project_are_invisible(
    auth_client, ready_project, db_session, second_user
):
    other_project = projects_repo.create(db_session, second_user.id, "پروژهٔ کاربر دیگر")
    other_chat = sessions_repo.create(
        db_session, project_id=other_project.id, user_id=second_user.id, title="مال دیگری"
    )
    other_message = messages_repo.create_user_message(
        db_session,
        session_id=other_chat.id,
        project_id=other_project.id,
        user_id=second_user.id,
        content="محرمانه",
    )

    # مسیر پروژه‌ی من با شناسه‌های پروژه/گفتگوی کاربر دیگر: «پیدا نشد».
    assert (
        auth_client.get(
            _api(ready_project.id) + f"/sessions/{other_chat.id}/messages/{other_message.id}"
        ).status_code
        == 404
    )
    # و پرسش روی گفتگوی کاربر دیگر هم ۴۰۴ می‌دهد (نه ۴۰۳ -- وجودش لو نمی‌رود).
    response = auth_client.post(
        _api(ready_project.id) + f"/sessions/{other_chat.id}/ask", json={"question": "سؤال"}
    )
    assert response.status_code == 404


def test_message_rows_carry_their_full_scope_on_the_row_itself(
    auth_client, ready_project, db_session, user
):
    """هر ردیف پیام هر سه شناسه را روی خودش دارد (کلید ترکیبی و پرس‌وجوی تک‌جدولی)."""
    session_id = _new_session(auth_client, ready_project.id)
    row = messages_repo.create_pending_assistant_message(
        db_session,
        session_id=session_id,
        project_id=ready_project.id,
        user_id=user.id,
    )

    assert (row.session_id, row.project_id, row.user_id) == (session_id, ready_project.id, user.id)
    # با هر یک از سه شرط نادرست، همان پرس‌وجوی تک‌جدولی چیزی برنمی‌گرداند.
    assert (
        messages_repo.get_by_id(
            db_session,
            row.id,
            session_id=session_id,
            project_id=ready_project.id,
            user_id=user.id,
        )
        is not None
    )
    assert (
        messages_repo.get_by_id(
            db_session,
            row.id,
            session_id="other-session",
            project_id=ready_project.id,
            user_id=user.id,
        )
        is None
    )
    assert (
        messages_repo.get_by_id(
            db_session,
            row.id,
            session_id=session_id,
            project_id=ready_project.id + 1,
            user_id=user.id,
        )
        is None
    )
    assert (
        messages_repo.get_by_id(
            db_session, row.id, session_id=session_id, project_id=ready_project.id, user_id=user.id + 1
        )
        is None
    )
    # ایندکس ترکیبی روی همین سه ستون وجود دارد (الگوی جدول‌های پایگاه‌دانش).
    from api.db import models

    index_columns = {
        tuple(column.name for column in index.columns)
        for index in models.ChatMessage.__table__.indexes
    }
    assert ("session_id", "project_id", "user_id") in index_columns


# ---------------------------------------------------------------------------
# ۶) باگ نمایش پیام کاربر (سرور + قرارداد سمت مرورگر)
# ---------------------------------------------------------------------------
def test_the_users_own_message_is_retrievable_the_moment_ask_returns(
    auth_client, ready_project, monkeypatch
):
    """سمت سرورِ رفع باگ نمایش: پرسش کاربر همان لحظه ذخیره و قابل‌خواندن است.

    ``TestClient`` جاوااسکریپت و DOM را اجرا نمی‌کند، بنابراین بخش *بصری* این
    رفع باگ در همین فایل جداگانه و در سطح منبع JS قفل می‌شود
    (``test_the_client_renders_the_users_message_before_it_awaits_the_server``):
    فایل ``web/static/js/financial_chatbot.js`` حباب پرسش را **پیش از** ارسال
    درخواست به سرور به فهرست پیام‌ها اضافه می‌کند -- و چون این ردیف سمت سرور هم
    واقعاً ساخته می‌شود، پیام پس از refresh هم باقی می‌ماند و تکراری نمی‌شود.
    """
    release = threading.Event()
    monkeypatch.setattr(chatbot, "ask_with_history", _blocking_ask(release=release))
    session_id = _new_session(auth_client, ready_project.id)

    started = _ask(auth_client, ready_project.id, session_id, "پرسش من")

    listed = auth_client.get(_api(ready_project.id) + f"/sessions/{session_id}/messages").json()
    assert [row["content"] for row in listed["messages"]] == ["پرسش من", ""]
    assert listed["messages"][0]["id"] == started["user_message_id"]

    release.set()
    _wait_for_message(auth_client, ready_project.id, session_id, started["assistant_message_id"])


def test_the_client_renders_the_users_message_before_it_awaits_the_server():
    """قرارداد سمت مرورگر: حباب پرسش کاربر، پیش از رفتن درخواست، رندر می‌شود.

    ترتیب در منبع JS بررسی می‌شود: ``appendRow("user"`` (رندر آنی) باید پیش از
    ``await fetch(... "/ask")`` بیاید. اگر کسی این را به «اول پاسخ سرور، بعد
    رندر» برگرداند، همان باگ گزارش‌شده برمی‌گردد و این تست می‌شکند.
    """
    source = JS_PATH.read_text(encoding="utf-8")
    assert not source.startswith("\ufeff")

    render_at = source.index('appendRow("user"')
    fetch_at = source.index('/ask"')
    assert render_at < fetch_at, "پرسش کاربر باید پیش از ارسال درخواست رندر شود"

    # شناسه‌ی ردیف ذخیره‌شده روی همان حباب می‌نشیند تا با تاریخچه‌ی سرور یکی شود
    # (نه یک پیام تکراری).
    assert "data-message-id" in source
    assert "data.user_message_id" in source

    # polling فقط سمت کلاینت را متوقف می‌کند: هیچ AbortController ای وجود ندارد
    # که با ترک صفحه، کار سمت سرور را بکشد.
    assert "new AbortController" not in source
    assert ".abort()" not in source
    assert "function stopPolling" in source
    # تاریخچه‌ی گفتگو از سرور خوانده می‌شود (نه از حافظه‌ی مرورگر).
    assert '"/messages"' in source
