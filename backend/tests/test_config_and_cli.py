from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from hisaab import cli
from hisaab.config import Settings
from hisaab.dburl import UnsupportedDatabaseError, normalize_database_url, redact
from hisaab.security import UnsafeBindError, check_bind_safety, verify_password


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("sqlite:///./data/hisaab.db", "sqlite:///./data/hisaab.db"),
        ("postgres://u:p@h:5432/d", "postgresql+psycopg://u:***@h:5432/d"),
        ("postgresql://u:p@h/d", "postgresql+psycopg://u:***@h/d"),
        ("mysql://u:p@h:3306/d", "mysql+pymysql://u:***@h:3306/d?charset=utf8mb4"),
        ("mariadb://u:p@h/d", "mysql+pymysql://u:***@h/d?charset=utf8mb4"),
        (
            "postgresql://postgres:p@db.abc.supabase.co:5432/postgres",
            "postgresql+psycopg://postgres:***@db.abc.supabase.co:5432/postgres?sslmode=require",
        ),
    ],
)
def test_database_urls_are_normalised(raw: str, expected: str) -> None:
    assert redact(normalize_database_url(raw)) == expected


@pytest.mark.parametrize("raw", ["", "oracle://u@h/d", "mongodb://h/d"])
def test_unsupported_databases_are_refused(raw: str) -> None:
    with pytest.raises(UnsupportedDatabaseError):
        normalize_database_url(raw)


def _settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[arg-type, call-arg]


def test_public_bind_without_password_is_refused() -> None:
    with pytest.raises(UnsafeBindError, match="0.0.0.0"):
        check_bind_safety(_settings(host="0.0.0.0"))  # noqa: S104


def test_public_bind_with_password_and_secret_is_allowed() -> None:
    check_bind_safety(_settings(host="0.0.0.0", password_hash="$argon2id$x", session_secret="s"))  # noqa: S104


def test_local_bind_never_blocks() -> None:
    check_bind_safety(_settings(host="127.0.0.1"))


def test_serve_exits_with_clear_error_when_unsafe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: _settings(host="0.0.0.0"))  # noqa: S104
    assert cli.main(["serve", "--no-migrate"]) == 3
    assert "Refusing to listen" in capsys.readouterr().err


def test_setup_writes_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    db = tmp_path / "x.db"
    monkeypatch.setattr(sys, "stdin", io.StringIO("a long enough password\n"))
    code = cli.main(
        [
            "setup",
            "--database-url",
            f"sqlite:///{db}",
            "--host",
            "127.0.0.1",
            "--password-stdin",
            "--env-file",
            str(env),
        ]
    )
    assert code == 0
    text = env.read_text()
    values = {
        k: v.strip("'")
        for k, v in (
            line.split("=", 1) for line in text.splitlines() if line and not line.startswith("#")
        )
    }
    assert verify_password(values["HISAAB_PASSWORD_HASH"], "a long enough password")
    assert "a long enough password" not in text
    assert len(values["HISAAB_SESSION_SECRET"]) > 40
    assert oct(env.stat().st_mode)[-3:] == "600"

    # never overwrite silently
    monkeypatch.setattr(sys, "stdin", io.StringIO("a long enough password\n"))
    assert (
        cli.main(
            [
                "setup",
                "--database-url",
                f"sqlite:///{db}",
                "--host",
                "127.0.0.1",
                "--password-stdin",
                "--env-file",
                str(env),
            ]
        )
        == 2
    )


def test_setup_rejects_short_password(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("short\n"))
    assert (
        cli.main(
            [
                "setup",
                "--database-url",
                f"sqlite:///{tmp_path / 'x.db'}",
                "--host",
                "127.0.0.1",
                "--password-stdin",
                "--env-file",
                str(tmp_path / ".env"),
            ]
        )
        == 2
    )
