from __future__ import annotations

from sqlalchemy import inspect, text

from hisaab.db import make_engine
from hisaab.migrate import downgrade, upgrade


def test_migrations_create_expected_tables(db_url: str) -> None:
    engine = make_engine(db_url)
    tables = set(inspect(engine).get_table_names())
    assert {"alembic_version", "app_meta", "secrets"} <= tables
    with engine.connect() as conn:
        origin = conn.execute(text("SELECT value FROM app_meta WHERE name = 'schema_origin'"))
        assert origin.scalar_one().startswith("hisaab")
    engine.dispose()


def test_migrations_round_trip(db_url: str) -> None:
    downgrade(db_url, "base")
    engine = make_engine(db_url)
    assert "secrets" not in inspect(engine).get_table_names()
    engine.dispose()
    upgrade(db_url)
    engine = make_engine(db_url)
    assert "secrets" in inspect(engine).get_table_names()
    engine.dispose()


def test_models_match_migrations(db_url: str) -> None:
    """Fails if someone changes models.py without writing a migration."""
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    from hisaab.models import Base

    engine = make_engine(db_url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        diff = compare_metadata(ctx, Base.metadata)
    engine.dispose()
    assert diff == []
