"""تست‌های احراز هویت: راه‌اندازی اولیه، ورود، خروج و محافظت از مسیرها."""
from __future__ import annotations

from api.auth import passwords
from api.repositories import users as users_repo


def test_hash_and_verify_password_roundtrip():
    hashed = passwords.hash_password("secret123")
    assert hashed != "secret123"
    assert passwords.verify_password("secret123", hashed)
    assert not passwords.verify_password("wrong", hashed)


def test_setup_creates_first_user_and_logs_in(client):
    response = client.post(
        "/setup",
        data={"username": "admin", "password": "secret123", "password_confirm": "secret123"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"

    # بلافاصله وارد شده است
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "پروژه‌های من" in dashboard.text


def test_setup_page_redirects_when_user_exists(client, user):
    response = client.get("/setup", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_setup_post_rejected_after_first_user(client, user, db_session):
    response = client.post(
        "/setup",
        data={"username": "intruder", "password": "secret123", "password_confirm": "secret123"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert users_repo.get_by_username(db_session, "intruder") is None


def test_setup_validates_password_confirmation(client):
    response = client.post(
        "/setup",
        data={"username": "admin", "password": "secret123", "password_confirm": "different"},
    )
    assert response.status_code == 400
    assert "تکرار رمز عبور" in response.text


def test_setup_rejects_short_password(client):
    response = client.post(
        "/setup",
        data={"username": "admin", "password": "123", "password_confirm": "123"},
    )
    assert response.status_code == 400


def test_login_with_wrong_password_fails(client, user):
    response = client.post("/login", data={"username": "tester", "password": "nope"})
    assert response.status_code == 400
    assert "نادرست است" in response.text


def test_login_with_unknown_user_fails_same_way(client, user):
    response = client.post("/login", data={"username": "ghost", "password": "secret123"})
    assert response.status_code == 400
    assert "نادرست است" in response.text


def test_login_redirects_to_requested_page(client, user):
    response = client.post(
        "/login",
        data={"username": "tester", "password": "secret123", "next": "/projects/7"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/projects/7"


def test_login_next_cannot_leave_the_site(client, user):
    response = client.post(
        "/login",
        data={"username": "tester", "password": "secret123", "next": "https://evil.example/x"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/"


def test_logout_clears_session(auth_client):
    assert auth_client.get("/").status_code == 200

    response = auth_client.post("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    assert auth_client.get("/", follow_redirects=False).status_code == 303


def test_dashboard_redirects_to_login_with_next(client):
    response = client.get("/projects/1", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?next=")


def test_api_requires_login(client, project):
    response = client.get(f"/api/projects/{project.id}/jobs")
    assert response.status_code == 401

    response = client.post(f"/api/projects/{project.id}/checklist/jobs", data={})
    assert response.status_code == 401


def test_login_page_offers_setup_when_no_users(client):
    page = client.get("/login")
    assert page.status_code == 200
    assert "/setup" in page.text


def test_login_page_hides_setup_when_users_exist(client, user):
    page = client.get("/login")
    assert page.status_code == 200
    assert "راه‌اندازی اولیه و ساخت کاربر راهبر" not in page.text
