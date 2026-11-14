"""App metadata and encrypted secrets.

Revision ID: 0001
Revises:
Create Date: 2026-11-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_meta",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_app_meta")),
    )
    op.create_table(
        "secrets",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_secrets")),
    )
    op.bulk_insert(
        sa.table("app_meta", sa.column("name", sa.String), sa.column("value", sa.Text)),
        [{"name": "schema_origin", "value": "hisaab 0.1.0"}],
    )


def downgrade() -> None:
    op.drop_table("secrets")
    op.drop_table("app_meta")
