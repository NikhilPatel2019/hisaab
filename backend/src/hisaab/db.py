"""Database engine and session handling."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from hisaab.dburl import dialect_of, ensure_sqlite_dir, normalize_database_url


def make_engine(database_url: str) -> Engine:
    url = normalize_database_url(database_url)
    ensure_sqlite_dir(url)
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if dialect_of(url) == "sqlite":
        kwargs = {"connect_args": {"check_same_thread": False}}
    engine = create_engine(url, **kwargs)

    if dialect_of(url) == "sqlite":

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.sessionmaker
    with factory() as session:
        yield session
