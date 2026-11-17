"""Shared fixtures. Set HISAAB_TEST_DATABASE_URL to run the suite against Postgres or MySQL."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from hisaab.config import Settings
from hisaab.ledger.backup import wipe_ledger
from hisaab.main import create_app
from hisaab.migrate import downgrade, upgrade
from hisaab.models import AppMeta, Connector, DocRule, Document, DocumentSighting, Secret
from hisaab.secretstore import new_key
from hisaab.security import hash_password

PASSWORD = "correct horse battery staple"
CSRF = {"X-Hisaab": "1"}


@pytest.fixture(scope="session")
def password_hash() -> str:
    return hash_password(PASSWORD)


@pytest.fixture(scope="session")
def db_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    url = os.environ.get("HISAAB_TEST_DATABASE_URL")
    if not url:
        path: Path = tmp_path_factory.mktemp("db") / "test.db"
        url = f"sqlite:///{path}"
    downgrade(url, "base")  # start clean even if a previous run crashed
    upgrade(url)
    yield url
    downgrade(url, "base")


@pytest.fixture
def settings(db_url: str, password_hash: str, tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=db_url,
        password_hash=password_hash,
        session_secret="test-session-secret-" + "x" * 32,
        secret_key=new_key(),
        vault_dir=str(tmp_path / "vault"),
    )


def wipe_all(s: Session) -> None:
    for model in (DocumentSighting, Document, Connector, DocRule):
        s.execute(delete(model))
    s.execute(delete(AppMeta).where(AppMeta.name.like("unlock.%")))
    s.execute(delete(Secret))
    wipe_ledger(s)
    s.commit()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    with app.state.sessionmaker() as s:
        wipe_all(s)
    with TestClient(app) as c:
        yield c
    app.state.engine.dispose()


@pytest.fixture
def logged_in(client: TestClient) -> TestClient:
    r = client.post("/api/auth/login", json={"password": PASSWORD}, headers=CSRF)
    assert r.status_code == 204, r.text
    return client
