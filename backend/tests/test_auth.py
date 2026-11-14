from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hisaab.config import Settings
from hisaab.main import create_app
from hisaab.security import LoginLimiter, hash_password, verify_password

from .conftest import CSRF, PASSWORD


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/api/me"), ("get", "/api/secrets"), ("delete", "/api/secrets/x")],
)
def test_private_routes_need_login(client: TestClient, method: str, path: str) -> None:
    r = client.request(method, path, headers=CSRF)
    assert r.status_code == 401


def test_health_and_session_are_public(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/auth/session").json() == {
        "authenticated": False,
        "setup_required": False,
    }


def test_login_then_me_then_logout(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"password": PASSWORD}, headers=CSRF)
    assert r.status_code == 204
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    me = client.get("/api/me").json()
    assert me["database"] in {"sqlite", "postgresql", "mysql"}
    assert client.post("/api/auth/logout", headers=CSRF).status_code == 204
    assert client.get("/api/me").status_code == 401


def test_wrong_password_is_401(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"password": "nope"}, headers=CSRF)
    assert r.status_code == 401


def test_login_is_rate_limited(client: TestClient) -> None:
    for _ in range(5):
        client.post("/api/auth/login", json={"password": "nope"}, headers=CSRF)
    r = client.post("/api/auth/login", json={"password": PASSWORD}, headers=CSRF)
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0


def test_state_changing_calls_need_csrf_header(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"password": PASSWORD})
    assert r.status_code == 403


def test_no_password_means_setup_required(settings: Settings) -> None:
    app = create_app(settings.model_copy(update={"password_hash": None}))
    with TestClient(app) as c:
        assert c.get("/api/auth/session").json()["setup_required"] is True
        r = c.post("/api/auth/login", json={"password": PASSWORD}, headers=CSRF)
        assert r.status_code == 503


def test_security_headers(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_password_hashing() -> None:
    h = hash_password(PASSWORD)
    assert h.startswith("$argon2")
    assert verify_password(h, PASSWORD)
    assert not verify_password(h, PASSWORD + "!")
    assert not verify_password("not-a-hash", PASSWORD)
    with pytest.raises(ValueError):
        hash_password("short")


def test_limiter_window_expires() -> None:
    lim = LoginLimiter(max_attempts=2, window_seconds=10)
    lim.record_failure("a", now=0)
    lim.record_failure("a", now=1)
    assert lim.retry_after("a", now=2) > 0
    assert lim.retry_after("b", now=2) == 0
    assert lim.retry_after("a", now=11.5) == 0
