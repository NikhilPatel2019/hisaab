"""Tables.

Money is stored as whole paise in a BIGINT: no floats, and the same on every database.
Amounts are signed from the account's point of view: money out is negative. A credit card's
balance is therefore negative while you owe the bank, so net worth is a plain sum.

Rules that keep one schema working on SQLite, PostgreSQL and MySQL:
- every string column has a length (MySQL needs it for indexes),
- constraint names come from a naming convention (Alembic batch mode needs them),
- timestamps are stored in UTC.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class AppMeta(Base):
    __tablename__ = "app_meta"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class Secret(Base):
    """A value we must keep but must never show: bank PDF passwords, broker API keys."""

    __tablename__ = "secrets"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# --- ledger -------------------------------------------------------------------------------

ACCOUNT_KINDS = (
    "bank",
    "cash",
    "wallet",
    "deposit",
    "investment",
    "retirement",
    "property",
    "other_asset",
    "credit_card",
    "loan",
    "other_liability",
)
LIABILITY_KINDS = frozenset({"credit_card", "loan", "other_liability"})
TRACKING_MODES = ("transactions", "balance")
CATEGORY_KINDS = ("expense", "income", "transfer", "investment")
TXN_SOURCES = ("manual", "csv", "demo", "restore", "statement")


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20))
    # "transactions": balance = opening + sum of transactions.
    # "balance": you record what it's worth now and then (EPF, FD, a house).
    tracking: Mapped[str] = mapped_column(String(20), default="transactions")
    institution: Mapped[str | None] = mapped_column(String(100), nullable=True)
    mask: Mapped[str | None] = mapped_column(String(8), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    opening_balance_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="account", passive_deletes=True
    )

    @property
    def is_liability(self) -> bool:
        return self.kind in LIABILITY_KINDS


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("kind", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(12))
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )


class ImportBatch(Base):
    """One CSV file brought into one account. Lets you see what came from where, and undo it."""

    __tablename__ = "imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(255))
    file_sha256: Mapped[str] = mapped_column(String(64))
    rows_added: Mapped[int] = mapped_column(Integer, default=0)
    rows_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    rows_skipped: Mapped[int] = mapped_column(Integer, default=0)
    mapping: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("account_id", "dedupe_hash"),
        Index("ix_transactions_account_posted", "account_id", "posted_on"),
        Index("ix_transactions_posted", "posted_on"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    posted_on: Mapped[date] = mapped_column(Date)
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    description: Mapped[str] = mapped_column(String(500))
    payee: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    import_id: Mapped[int | None] = mapped_column(
        ForeignKey("imports.id", ondelete="SET NULL"), nullable=True
    )
    # Same file imported twice → same hashes → nothing added the second time.
    dedupe_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    account: Mapped[Account] = relationship(back_populates="transactions")
    category: Mapped[Category | None] = relationship()


class BalanceSnapshot(Base):
    """What an account was worth on a day: the value of a balance-tracked account,
    or a checkpoint ("the bank says ₹13,300") for a transaction-tracked one."""

    __tablename__ = "balance_snapshots"
    __table_args__ = (UniqueConstraint("account_id", "as_of"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    as_of: Mapped[date] = mapped_column(Date)
    balance_paise: Mapped[int] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)


# --- documents (statements, CAS, tax forms) -------------------------------------------------

DOC_KINDS = (
    "bank_statement",
    "card_statement",
    "cas",
    "broker",
    "tax",
    "payslip",
    "insurance",
    "other",
    "unknown",
)
DOC_STATUSES = ("needs_password", "needs_review", "filed", "ignored")
DOC_SOURCES = ("upload", "drive", "imap", "folder")


class Document(Base):
    """One unique file, stored once in the vault exactly as received (named by its SHA-256).

    Decrypted copies are never stored: they're made in memory when you open one.
    """

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    filename: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(100), default="application/pdf")
    source: Mapped[str] = mapped_column(String(20))
    sender: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    # Which stored password or recipe opened it. An id, never the password itself.
    unlocked_with: Mapped[str | None] = mapped_column(String(100), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="needs_review")
    kind: Mapped[str] = mapped_column(String(20), default="unknown")
    institution: Mapped[str | None] = mapped_column(String(100), nullable=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    classified_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DocumentSighting(Base):
    """Every time a source showed us a file. Lets re-runs skip what they've already seen,
    and counts duplicates (the same PDF from Drive and from email is stored once)."""

    __tablename__ = "document_sightings"
    __table_args__ = (UniqueConstraint("source", "source_ref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(20))
    source_ref: Mapped[str] = mapped_column(String(400))
    outcome: Mapped[str] = mapped_column(String(20))
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Connector(Base):
    __tablename__ = "connectors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100))
    # Non-secret settings as JSON. Credentials live in the encrypted secret store.
    config: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_summary: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DocRule(Base):
    """'Always file PDFs like this as …'. Every condition given must match."""

    __tablename__ = "doc_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    match_sender: Mapped[str | None] = mapped_column(String(255), nullable=True)
    match_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    match_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kind: Mapped[str] = mapped_column(String(20))
    institution: Mapped[str | None] = mapped_column(String(100), nullable=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
