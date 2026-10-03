"""Security basics: sign-in errors, the session cookie, the CSRF header and server-side role scopes."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_ready")

PASSWORD = "relay2026"


def login(client, email, code=None, password=PASSWORD):
    client.cookies.clear()
    return client.post("/api/auth/login", json={"email": email, "password": password, "workspace": code})


def test_wrong_password_gets_a_human_answer(client):
    r = login(client, "nirosha@waypoint.lk", password="not-the-password")
    assert r.status_code == 401
    assert "don't match" in r.json()["detail"]


def test_unknown_workspace_code_is_refused(client):
    r = login(client, "nirosha@waypoint.lk", code="RLY-ZZZZ")
    assert r.status_code == 404


def test_session_cookie_is_http_only_and_same_site(client):
    r = login(client, "nirosha@waypoint.lk")
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_signed_out_requests_are_refused(client):
    client.cookies.clear()
    assert client.get("/api/dispatch/snapshot").status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_writes_need_the_client_header(client):
    assert login(client, "fathima@waypoint.lk").status_code == 200
    # a cross-site form can send the cookie but cannot set a custom header
    r = client.post("/api/store/notices/read", headers={"x-relay-client": "somewhere-else"})
    assert r.status_code == 403
    assert client.post("/api/store/notices/read").status_code == 200


SCOPES = {
    "nirosha@waypoint.lk": ["/api/dispatch/snapshot", "/api/dispatch/outlook"],
    "kasun@waypoint.lk": ["/api/dock/queue"],
    "sunil@waypoint.lk": ["/api/driver/run"],
    "fathima@waypoint.lk": ["/api/store/home", "/api/store/catalog"],
}


@pytest.mark.parametrize("email", list(SCOPES))
def test_each_role_reaches_only_its_own_api(client, sandbox_code, email):
    assert login(client, email, sandbox_code).status_code == 200
    for path in SCOPES[email]:
        assert client.get(path).status_code == 200, path
    for other, paths in SCOPES.items():
        if other != email:
            for path in paths:
                assert client.get(path).status_code == 403, f"{email} reached {path}"


def test_only_the_dispatcher_moves_the_demo_clock(client, sandbox_code):
    assert login(client, "sunil@waypoint.lk", sandbox_code).status_code == 200
    assert client.post("/api/demo/clock", json={"op": "advance", "minutes": 15}).status_code == 403
