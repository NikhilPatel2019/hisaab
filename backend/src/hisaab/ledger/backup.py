"""Export everything to one JSON file, and restore it into any supported database.

IDs are remapped on restore, so a backup from SQLite loads into PostgreSQL or MySQL
(or a fresh SQLite) and gives the same balances. Stored secrets are not exported:
they're encrypted with your HISAAB_SECRET_KEY and should stay with it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from hisaab import __version__
from hisaab.models import Account, BalanceSnapshot, Category, ImportBatch, Transaction

FORMAT = "hisaab-backup"
VERSION = 1

_ACCOUNT_FIELDS = [
    "id",
    "name",
    "kind",
    "tracking",
    "institution",
    "mask",
    "currency",
    "opening_balance_paise",
    "archived",
    "is_demo",
    "notes",
]
_TXN_FIELDS = [
    "id",
    "account_id",
    "posted_on",
    "amount_paise",
    "description",
    "payee",
    "reference",
    "category_id",
    "notes",
    "source",
    "import_id",
    "dedupe_hash",
]
_IMPORT_FIELDS = [
    "id",
    "account_id",
    "filename",
    "file_sha256",
    "rows_added",
    "rows_duplicate",
    "rows_skipped",
    "mapping",
]
_SNAP_FIELDS = ["id", "account_id", "as_of", "balance_paise", "source", "note"]


class BackupError(Exception):
    pass


def _row(obj: object, fields: list[str]) -> dict[str, Any]:
    out = {}
    for f in fields:
        v = getattr(obj, f)
        out[f] = v.isoformat() if isinstance(v, date) else v
    return out


def export_all(session: Session) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "version": VERSION,
        "app_version": __version__,
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "categories": [
            _row(c, ["id", "name", "kind", "parent_id"])
            for c in session.scalars(select(Category).order_by(Category.id))
        ],
        "accounts": [
            _row(a, _ACCOUNT_FIELDS) for a in session.scalars(select(Account).order_by(Account.id))
        ],
        "imports": [
            _row(i, _IMPORT_FIELDS)
            for i in session.scalars(select(ImportBatch).order_by(ImportBatch.id))
        ],
        "transactions": [
            _row(t, _TXN_FIELDS)
            for t in session.scalars(select(Transaction).order_by(Transaction.id))
        ],
        "snapshots": [
            _row(s, _SNAP_FIELDS)
            for s in session.scalars(select(BalanceSnapshot).order_by(BalanceSnapshot.id))
        ],
    }


def ledger_is_empty(session: Session) -> bool:
    return not session.scalar(select(func.count()).select_from(Account))


def wipe_ledger(session: Session) -> None:
    for model in (Transaction, BalanceSnapshot, ImportBatch, Account):
        session.execute(delete(model))
    session.flush()


def restore_all(session: Session, data: dict[str, Any], replace: bool = False) -> dict[str, int]:
    if data.get("format") != FORMAT:
        raise BackupError("This isn't a hisaab backup file.")
    if int(data.get("version", 0)) > VERSION:
        raise BackupError("This backup is from a newer hisaab. Update first.")
    if not ledger_is_empty(session):
        if not replace:
            raise BackupError(
                "This database already has accounts. Restore into an empty one, "
                "or pass --replace to wipe it first."
            )
        wipe_ledger(session)

    # Categories: match on (kind, name); create any that are missing.
    existing = {(c.kind, c.name): c for c in session.scalars(select(Category))}
    cat_map: dict[int, int] = {}
    pending_parents: list[tuple[Category, int]] = []
    for c in data.get("categories", []):
        cat = existing.get((c["kind"], c["name"]))
        if cat is None:
            cat = Category(name=c["name"], kind=c["kind"])
            session.add(cat)
            session.flush()
            existing[(cat.kind, cat.name)] = cat
        cat_map[c["id"]] = cat.id
        if c.get("parent_id"):
            pending_parents.append((cat, c["parent_id"]))
    for cat, old_parent in pending_parents:
        cat.parent_id = cat_map.get(old_parent)

    acct_map: dict[int, int] = {}
    for a in data.get("accounts", []):
        fields = {k: v for k, v in a.items() if k != "id"}
        account = Account(**fields)
        session.add(account)
        session.flush()
        acct_map[a["id"]] = account.id

    import_map: dict[int, int] = {}
    for i in data.get("imports", []):
        fields = {k: v for k, v in i.items() if k != "id"}
        fields["account_id"] = acct_map[i["account_id"]]
        batch = ImportBatch(**fields)
        session.add(batch)
        session.flush()
        import_map[i["id"]] = batch.id

    txns = []
    for t in data.get("transactions", []):
        fields = {k: v for k, v in t.items() if k != "id"}
        fields["account_id"] = acct_map[t["account_id"]]
        fields["posted_on"] = date.fromisoformat(t["posted_on"])
        fields["category_id"] = cat_map.get(t["category_id"]) if t.get("category_id") else None
        fields["import_id"] = import_map.get(t["import_id"]) if t.get("import_id") else None
        txns.append(Transaction(**fields))
    for start in range(0, len(txns), 1000):
        session.add_all(txns[start : start + 1000])
        session.flush()

    for s in data.get("snapshots", []):
        fields = {k: v for k, v in s.items() if k != "id"}
        fields["account_id"] = acct_map[s["account_id"]]
        fields["as_of"] = date.fromisoformat(s["as_of"])
        session.add(BalanceSnapshot(**fields))
    session.commit()
    return {
        "accounts": len(acct_map),
        "transactions": len(txns),
        "snapshots": len(data.get("snapshots", [])),
        "imports": len(import_map),
    }
