"""Turn whatever database URL a user pastes into one SQLAlchemy can use.

People copy URLs from Supabase, Railway, a home server or a tutorial. They come
as ``postgres://``, ``postgresql://``, ``mysql://`` or ``mariadb://`` without a
driver. We pick the driver, add the settings each database needs, and refuse
anything we don't support with a message that says what to do.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

SUPPORTED = ("sqlite", "postgresql", "mysql")

_DRIVERS = {
    "postgres": "postgresql+psycopg",
    "postgresql": "postgresql+psycopg",
    "postgresql+psycopg": "postgresql+psycopg",
    "postgresql+psycopg2": "postgresql+psycopg",
    "mysql": "mysql+pymysql",
    "mariadb": "mysql+pymysql",
    "mysql+pymysql": "mysql+pymysql",
    "mariadb+pymysql": "mysql+pymysql",
    "sqlite": "sqlite",
    "sqlite+pysqlite": "sqlite",
}


class UnsupportedDatabaseError(ValueError):
    pass


def normalize_database_url(raw: str) -> URL:
    text = raw.strip()
    if not text:
        raise UnsupportedDatabaseError("The database URL is empty.")
    # SQLAlchemy dropped the `postgres://` alias; many hosts still hand it out.
    if text.startswith("postgres://"):
        text = "postgresql://" + text[len("postgres://") :]
    try:
        url = make_url(text)
    except ArgumentError as exc:
        raise UnsupportedDatabaseError(f"Could not read the database URL: {exc}") from exc

    driver = _DRIVERS.get(url.drivername)
    if driver is None:
        raise UnsupportedDatabaseError(
            f"'{url.drivername}' isn't supported. Use SQLite, PostgreSQL (including "
            "Supabase) or MySQL/MariaDB."
        )
    url = url.set(drivername=driver)
    query = dict(url.query)

    if driver.startswith("mysql"):
        query.setdefault("charset", "utf8mb4")
    if driver.startswith("postgresql") and _is_supabase(url.host):
        query.setdefault("sslmode", "require")
    return url.set(query=query)


def dialect_of(url: URL) -> str:
    return url.get_backend_name()


def ensure_sqlite_dir(url: URL) -> None:
    """Create the folder for a file-based SQLite database."""
    if dialect_of(url) != "sqlite" or not url.database or url.database == ":memory:":
        return
    Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)


def redact(url: URL) -> str:
    return url.render_as_string(hide_password=True)


def _is_supabase(host: str | None) -> bool:
    return bool(host) and (host or "").endswith((".supabase.co", ".supabase.com"))
