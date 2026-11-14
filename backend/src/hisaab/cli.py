"""Command line: `hisaab setup`, `hisaab serve`, `hisaab migrate`, `hisaab check`."""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys
import time
from pathlib import Path
from typing import TextIO

from sqlalchemy import text

from hisaab import __version__
from hisaab.config import DEFAULT_DATABASE_URL, Settings, get_settings
from hisaab.db import make_engine
from hisaab.dburl import UnsupportedDatabaseError, normalize_database_url, redact
from hisaab.secretstore import new_key
from hisaab.security import MIN_PASSWORD_LENGTH, UnsafeBindError, check_bind_safety, hash_password

DB_CHOICES = {
    "1": ("This computer (SQLite, nothing to install)", DEFAULT_DATABASE_URL),
    "2": (
        "PostgreSQL on a server you run",
        "postgresql://hisaab:PASSWORD@192.168.1.20:5432/hisaab",
    ),
    "3": (
        "MySQL or MariaDB on a server you run",
        "mysql://hisaab:PASSWORD@192.168.1.20:3306/hisaab",
    ),
    "4": (
        "Your own Supabase project",
        "postgresql://postgres:PASSWORD@db.YOURPROJECT.supabase.co:5432/postgres",
    ),
}


def _say(out: TextIO, msg: str = "") -> None:
    print(msg, file=out)


def _ask(out: TextIO, prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    print(f"{prompt}{suffix}: ", end="", file=out, flush=True)
    answer = sys.stdin.readline().strip()
    return answer or (default or "")


def test_connection(database_url: str) -> str | None:
    """Return None if we can connect, otherwise a readable error."""
    try:
        engine = make_engine(database_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
    except UnsupportedDatabaseError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - show the driver's message to the user
        return f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
    return None


def render_env(values: dict[str, str]) -> str:
    lines = ["# Written by `hisaab setup`. Keep this file private: it holds your keys."]
    for key, value in values.items():
        lines.append(f"{key}='{value}'" if ("$" in value or " " in value) else f"{key}={value}")
    return "\n".join(lines) + "\n"


def cmd_setup(args: argparse.Namespace) -> int:
    # With --stdout the .env goes to stdout, so prompts go to stderr.
    out = sys.stderr if args.stdout else sys.stdout
    _say(out, f"hisaab {__version__} setup — your money stays on your machine.\n")

    db_url = args.database_url
    if not db_url:
        _say(out, "Where should your data live?")
        for key, (label, _) in DB_CHOICES.items():
            _say(out, f"  {key}. {label}")
        choice = _ask(out, "Choose 1-4", "1")
        label, example = DB_CHOICES.get(choice, DB_CHOICES["1"])
        db_url = example if choice in ("", "1") else _ask(out, f"Connection URL (e.g. {example})")
    try:
        normalize_database_url(db_url)
    except UnsupportedDatabaseError as exc:
        _say(out, f"✗ {exc}")
        return 2

    if not args.skip_db_check:
        error = test_connection(db_url)
        if error:
            _say(out, f"✗ Couldn't connect to {redact(normalize_database_url(db_url))}: {error}")
            return 2
        _say(out, "✓ Database reachable.")

    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        while True:
            password = getpass.getpass(
                f"Login password (min {MIN_PASSWORD_LENGTH} characters): ", stream=out
            )
            if len(password) < MIN_PASSWORD_LENGTH:
                _say(out, f"Use at least {MIN_PASSWORD_LENGTH} characters.")
                continue
            if getpass.getpass("Again: ", stream=out) == password:
                break
            _say(out, "Those didn't match. Try again.")
    try:
        password_hash = hash_password(password)
    except ValueError as exc:
        _say(out, f"✗ {exc}")
        return 2

    host = args.host
    if not host:
        where = _ask(out, "Open it only on this computer (1) or from other devices too (2)?", "1")
        host = "0.0.0.0" if where == "2" else "127.0.0.1"  # noqa: S104 - user asked for it

    values = {
        "HISAAB_DATABASE_URL": db_url,
        "HISAAB_HOST": host,
        "HISAAB_PORT": str(args.port),
        "HISAAB_PASSWORD_HASH": password_hash,
        "HISAAB_SESSION_SECRET": secrets.token_urlsafe(48),
        "HISAAB_SECRET_KEY": new_key(),
    }
    content = render_env(values)

    if args.stdout:
        sys.stdout.write(content)
        return 0
    target = Path(args.env_file)
    if target.exists() and not args.force:
        _say(
            out,
            f"✗ {target} already exists. Re-run with --force to replace it "
            "(stored secrets need the old HISAAB_SECRET_KEY).",
        )
        return 2
    target.write_text(content, encoding="utf-8")
    target.chmod(0o600)
    _say(out, f"✓ Wrote {target}. Start with `hisaab serve` (or `make dev`).")
    return 0


def _wait_for_database(database_url: str, timeout: float) -> bool:
    """Containers often start before their database; retry for a while before giving up."""
    deadline = time.monotonic() + timeout
    while True:
        error = test_connection(database_url)
        if error is None:
            return True
        if time.monotonic() >= deadline:
            print(f"✗ Database not reachable: {error}", file=sys.stderr)
            return False
        print(f"… waiting for the database ({error})", file=sys.stderr)
        time.sleep(2)


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from hisaab.main import create_app
    from hisaab.migrate import upgrade

    settings = get_settings()
    try:
        check_bind_safety(settings)
    except UnsafeBindError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 3
    if not _wait_for_database(settings.database_url, timeout=args.wait):
        return 4
    if not args.no_migrate:
        upgrade(settings.database_url)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        log_level="info",
    )
    return 0


def cmd_migrate(_args: argparse.Namespace) -> int:
    from hisaab.migrate import upgrade

    upgrade(get_settings().database_url)
    print("✓ Database is up to date.")
    return 0


def cmd_check(_args: argparse.Namespace) -> int:
    s: Settings = get_settings()
    rows = [
        ("Database", redact(normalize_database_url(s.database_url))),
        ("Listening on", f"{s.host}:{s.port}"),
        ("Password set", "yes" if s.password_hash else "no"),
        ("Session secret", "set" if s.session_secret else "missing"),
        ("Secret key", "set" if s.secret_key else "missing"),
    ]
    for k, v in rows:
        print(f"{k:16} {v}")
    try:
        check_bind_safety(s)
    except UnsafeBindError as exc:
        print(f"✗ {exc}")
        return 3
    return 0


def cmd_hash_password(_args: argparse.Namespace) -> int:
    print(hash_password(getpass.getpass("Password: ")))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hisaab", description="Self-hosted finance dashboard.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("setup", help="create your .env: database, password and keys")
    s.add_argument("--database-url")
    s.add_argument("--host")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--env-file", default=".env")
    s.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    s.add_argument("--stdout", action="store_true", help="print the .env instead of writing it")
    s.add_argument("--skip-db-check", action="store_true")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_setup)

    v = sub.add_parser("serve", help="run migrations and start the server")
    v.add_argument("--no-migrate", action="store_true")
    v.add_argument("--wait", type=float, default=60, help="seconds to wait for the database")
    v.set_defaults(func=cmd_serve)

    sub.add_parser("migrate", help="bring the database schema up to date").set_defaults(
        func=cmd_migrate
    )
    sub.add_parser("check", help="show configuration (never secrets)").set_defaults(func=cmd_check)
    sub.add_parser("hash-password", help="print an argon2 hash").set_defaults(
        func=cmd_hash_password
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    code: int = args.func(args)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
