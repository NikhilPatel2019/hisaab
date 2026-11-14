"""Run Alembic migrations from code, so users never need an alembic.ini."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from hisaab.dburl import ensure_sqlite_dir, normalize_database_url

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(database_url: str) -> Config:
    url = normalize_database_url(database_url)
    ensure_sqlite_dir(url)
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    # '%' must be escaped for ConfigParser; passwords often contain it.
    cfg.set_main_option(
        "sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%")
    )
    return cfg


def upgrade(database_url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)


def downgrade(database_url: str, revision: str = "base") -> None:
    command.downgrade(alembic_config(database_url), revision)
