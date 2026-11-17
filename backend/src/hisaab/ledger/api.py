"""Ledger HTTP API: accounts, categories, transactions, snapshots, imports, dashboard, backup."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, ClassVar, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from hisaab.api import require_user
from hisaab.db import get_session
from hisaab.ledger import balances, csvimport
from hisaab.ledger.backup import export_all, ledger_is_empty
from hisaab.ledger.demo import load_demo, remove_demo
from hisaab.ledger.text import guess_payee
from hisaab.models import (
    ACCOUNT_KINDS,
    CATEGORY_KINDS,
    LIABILITY_KINDS,
    Account,
    BalanceSnapshot,
    Category,
    ImportBatch,
    Transaction,
)

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])
Db = Annotated[Session, Depends(get_session)]

AccountKind = Literal[
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
]
Tracking = Literal["transactions", "balance"]
CategoryKind = Literal["expense", "income", "transfer", "investment"]
assert set(AccountKind.__args__) == set(ACCOUNT_KINDS)  # type: ignore[attr-defined]
assert set(CategoryKind.__args__) == set(CATEGORY_KINDS)  # type: ignore[attr-defined]


def today(request: Request) -> date:
    return datetime.now(ZoneInfo(request.app.state.settings.timezone)).date()


def _get(db: Session, model: type[Any], id_: int, what: str) -> Any:
    obj = db.get(model, id_)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No {what} with id {id_}.")
    return obj


class _Patch(BaseModel):
    """PATCH bodies: leave a field out to keep it; null is only allowed where it means 'clear'."""

    not_null: ClassVar[tuple[str, ...]] = ()

    @model_validator(mode="after")
    def _no_nulls(self) -> _Patch:
        for name in self.not_null:
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} can't be empty")
        return self


# --- accounts -------------------------------------------------------------------------------


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: AccountKind
    tracking: Tracking = "transactions"
    institution: str | None = Field(default=None, max_length=100)
    mask: str | None = Field(default=None, max_length=8)
    opening_balance_paise: int = 0
    notes: str | None = None

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class AccountPatch(_Patch):
    not_null = ("name", "opening_balance_paise", "archived")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    institution: str | None = Field(default=None, max_length=100)
    mask: str | None = Field(default=None, max_length=8)
    opening_balance_paise: int | None = None
    archived: bool | None = None
    notes: str | None = None


class AccountOut(BaseModel):
    id: int
    name: str
    kind: AccountKind
    tracking: Tracking
    institution: str | None
    mask: str | None
    opening_balance_paise: int
    archived: bool
    is_demo: bool
    is_liability: bool
    notes: str | None
    balance_paise: int
    transactions: int
    last_activity: date | None


def _account_out(db: Session, a: Account, on: date, bal: dict[int, int]) -> AccountOut:
    count, last_txn = db.execute(
        select(func.count(Transaction.id), func.max(Transaction.posted_on)).where(
            Transaction.account_id == a.id
        )
    ).one()
    last_snap = db.scalar(
        select(func.max(BalanceSnapshot.as_of)).where(BalanceSnapshot.account_id == a.id)
    )
    last = max((d for d in (last_txn, last_snap) if d), default=None)
    return AccountOut(
        id=a.id,
        name=a.name,
        kind=a.kind,  # type: ignore[arg-type]
        tracking=a.tracking,  # type: ignore[arg-type]
        institution=a.institution,
        mask=a.mask,
        opening_balance_paise=a.opening_balance_paise,
        archived=a.archived,
        is_demo=a.is_demo,
        is_liability=a.kind in LIABILITY_KINDS,
        notes=a.notes,
        balance_paise=bal.get(a.id, 0),
        transactions=int(count),
        last_activity=last,
    )


@router.get("/accounts")
def list_accounts(request: Request, db: Db) -> list[AccountOut]:
    on = today(request)
    bal = balances.balances_on(db, on)
    rows = db.scalars(select(Account).order_by(Account.archived, Account.kind, Account.name))
    return [_account_out(db, a, on, bal) for a in rows]


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
def create_account(body: AccountIn, request: Request, db: Db) -> AccountOut:
    a = Account(**body.model_dump())
    db.add(a)
    db.commit()
    on = today(request)
    return _account_out(db, a, on, balances.balances_on(db, on))


@router.get("/accounts/{account_id}")
def get_account(account_id: int, request: Request, db: Db) -> AccountOut:
    a = _get(db, Account, account_id, "account")
    on = today(request)
    return _account_out(db, a, on, balances.balances_on(db, on))


@router.patch("/accounts/{account_id}")
def update_account(account_id: int, body: AccountPatch, request: Request, db: Db) -> AccountOut:
    a = _get(db, Account, account_id, "account")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(a, k, v)
    db.commit()
    on = today(request)
    return _account_out(db, a, on, balances.balances_on(db, on))


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(account_id: int, db: Db, force: bool = False) -> None:
    a = _get(db, Account, account_id, "account")
    used = db.scalar(select(func.count()).where(Transaction.account_id == a.id)) or 0
    used += db.scalar(select(func.count()).where(BalanceSnapshot.account_id == a.id)) or 0
    if used and not force:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This account has {used} entries. Archive it instead, or delete with force.",
        )
    db.delete(a)
    db.commit()


# --- categories -----------------------------------------------------------------------------


class CategoryOut(BaseModel):
    id: int
    name: str
    kind: CategoryKind
    parent_id: int | None


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    kind: CategoryKind


@router.get("/categories")
def list_categories(db: Db) -> list[CategoryOut]:
    return [
        CategoryOut(id=c.id, name=c.name, kind=c.kind, parent_id=c.parent_id)  # type: ignore[arg-type]
        for c in db.scalars(select(Category).order_by(Category.kind, Category.name))
    ]


@router.post("/categories", status_code=status.HTTP_201_CREATED)
def create_category(body: CategoryIn, db: Db) -> CategoryOut:
    name = body.name.strip()
    if db.scalar(select(Category).where(Category.kind == body.kind, Category.name == name)):
        raise HTTPException(status.HTTP_409_CONFLICT, "That category already exists.")
    c = Category(name=name, kind=body.kind)
    db.add(c)
    db.commit()
    return CategoryOut(id=c.id, name=c.name, kind=body.kind, parent_id=None)


# --- transactions ---------------------------------------------------------------------------


class TxnOut(BaseModel):
    id: int
    account_id: int
    account: str
    posted_on: date
    amount_paise: int
    description: str
    payee: str | None
    reference: str | None
    category_id: int | None
    category: str | None
    category_kind: CategoryKind | None
    notes: str | None
    source: str
    import_id: int | None


class TxnPage(BaseModel):
    items: list[TxnOut]
    total: int
    sum_paise: int


class TxnIn(BaseModel):
    account_id: int
    posted_on: date
    amount_paise: int = Field(description="Negative = money out of this account.")
    description: str = Field(min_length=1, max_length=500)
    payee: str | None = Field(default=None, max_length=200)
    category_id: int | None = None
    notes: str | None = None


class TxnPatch(_Patch):
    not_null = ("posted_on", "amount_paise", "description")
    posted_on: date | None = None
    amount_paise: int | None = None
    description: str | None = Field(default=None, min_length=1, max_length=500)
    payee: str | None = Field(default=None, max_length=200)
    category_id: int | None = None
    notes: str | None = None


def _txn_query() -> Any:
    return (
        select(Transaction, Account.name, Category.name, Category.kind)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(Category, Transaction.category_id == Category.id)
    )


def _txn_out(t: Transaction, account: str, cat: str | None, kind: str | None) -> TxnOut:
    return TxnOut(
        id=t.id,
        account_id=t.account_id,
        account=account,
        posted_on=t.posted_on,
        amount_paise=t.amount_paise,
        description=t.description,
        payee=t.payee,
        reference=t.reference,
        category_id=t.category_id,
        category=cat,
        category_kind=kind,  # type: ignore[arg-type]
        notes=t.notes,
        source=t.source,
        import_id=t.import_id,
    )


@router.get("/transactions")
def list_transactions(
    db: Db,
    account_id: int | None = None,
    category_id: int | None = None,
    uncategorised: bool = False,
    q: Annotated[str | None, Query(max_length=100)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TxnPage:
    filters = []
    if account_id is not None:
        filters.append(Transaction.account_id == account_id)
    if category_id is not None:
        filters.append(Transaction.category_id == category_id)
    if uncategorised:
        filters.append(Transaction.category_id.is_(None))
    if q:
        needle = q.lower()  # matched literally: % and _ are not wildcards here
        filters.append(
            or_(
                func.lower(Transaction.description).contains(needle, autoescape=True),
                func.lower(func.coalesce(Transaction.payee, "")).contains(needle, autoescape=True),
                func.lower(func.coalesce(Transaction.notes, "")).contains(needle, autoescape=True),
            )
        )
    if date_from:
        filters.append(Transaction.posted_on >= date_from)
    if date_to:
        filters.append(Transaction.posted_on <= date_to)

    total, total_sum = db.execute(
        select(func.count(Transaction.id), func.coalesce(func.sum(Transaction.amount_paise), 0))
        .select_from(Transaction)
        .where(*filters)
    ).one()
    rows = db.execute(
        _txn_query()
        .where(*filters)
        .order_by(Transaction.posted_on.desc(), Transaction.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return TxnPage(items=[_txn_out(*r) for r in rows], total=int(total), sum_paise=int(total_sum))


def _check_category(db: Session, category_id: int | None) -> None:
    if category_id is not None and db.get(Category, category_id) is None:
        raise HTTPException(422, "Unknown category.")


def _one_txn(db: Session, txn_id: int) -> TxnOut:
    row = db.execute(_txn_query().where(Transaction.id == txn_id)).one()
    return _txn_out(*row)


@router.post("/transactions", status_code=status.HTTP_201_CREATED)
def create_transaction(body: TxnIn, db: Db) -> TxnOut:
    account = _get(db, Account, body.account_id, "account")
    if account.tracking == "balance":
        raise HTTPException(
            422,
            "This account is tracked by balance. Add a balance update instead.",
        )
    _check_category(db, body.category_id)
    t = Transaction(
        **body.model_dump(exclude={"payee"}),
        payee=body.payee or guess_payee(body.description),
        source="manual",
    )
    db.add(t)
    db.commit()
    return _one_txn(db, t.id)


@router.patch("/transactions/{txn_id}")
def update_transaction(txn_id: int, body: TxnPatch, db: Db) -> TxnOut:
    t = _get(db, Transaction, txn_id, "transaction")
    changes = body.model_dump(exclude_unset=True)
    if "category_id" in changes:
        _check_category(db, changes["category_id"])
    for k, v in changes.items():
        setattr(t, k, v)
    db.commit()
    return _one_txn(db, t.id)


@router.delete("/transactions/{txn_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaction(txn_id: int, db: Db) -> None:
    db.delete(_get(db, Transaction, txn_id, "transaction"))
    db.commit()


# --- balance snapshots ----------------------------------------------------------------------


class SnapshotIn(BaseModel):
    as_of: date
    balance_paise: int
    note: str | None = Field(default=None, max_length=200)


class SnapshotOut(BaseModel):
    id: int
    as_of: date
    balance_paise: int
    note: str | None
    source: str
    ledger_paise: int | None = Field(
        description="For transaction-tracked accounts: what hisaab works out for that day."
    )


@router.get("/accounts/{account_id}/snapshots")
def list_snapshots(account_id: int, db: Db) -> list[SnapshotOut]:
    a = _get(db, Account, account_id, "account")
    out = []
    for s in db.scalars(
        select(BalanceSnapshot)
        .where(BalanceSnapshot.account_id == a.id)
        .order_by(BalanceSnapshot.as_of.desc())
    ):
        ledger = balances.ledger_balance(db, a, s.as_of) if a.tracking == "transactions" else None
        out.append(
            SnapshotOut(
                id=s.id,
                as_of=s.as_of,
                balance_paise=s.balance_paise,
                note=s.note,
                source=s.source,
                ledger_paise=ledger,
            )
        )
    return out


@router.post("/accounts/{account_id}/snapshots", status_code=status.HTTP_201_CREATED)
def upsert_snapshot(account_id: int, body: SnapshotIn, db: Db) -> SnapshotOut:
    a = _get(db, Account, account_id, "account")
    s = db.scalar(
        select(BalanceSnapshot).where(
            BalanceSnapshot.account_id == a.id, BalanceSnapshot.as_of == body.as_of
        )
    )
    if s is None:
        s = BalanceSnapshot(account_id=a.id, as_of=body.as_of, balance_paise=0)
        db.add(s)
    s.balance_paise = body.balance_paise
    s.note = body.note
    s.source = "manual"
    db.commit()
    ledger = balances.ledger_balance(db, a, s.as_of) if a.tracking == "transactions" else None
    return SnapshotOut(
        id=s.id,
        as_of=s.as_of,
        balance_paise=s.balance_paise,
        note=s.note,
        source=s.source,
        ledger_paise=ledger,
    )


@router.delete("/snapshots/{snapshot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_snapshot(snapshot_id: int, db: Db) -> None:
    db.delete(_get(db, BalanceSnapshot, snapshot_id, "balance update"))
    db.commit()


# --- imports --------------------------------------------------------------------------------


class ImportRequest(BaseModel):
    account_id: int
    filename: str = Field(min_length=1, max_length=255)
    content: str = Field(max_length=csvimport.MAX_CHARS)
    mapping: csvimport.Mapping | None = None


class ImportOut(BaseModel):
    id: int
    account_id: int
    account: str
    filename: str
    rows_added: int
    rows_duplicate: int
    rows_skipped: int
    created_at: datetime
    transactions_left: int


def _import_account(db: Session, account_id: int) -> Account:
    account: Account = _get(db, Account, account_id, "account")
    if account.tracking == "balance":
        raise HTTPException(
            422,
            "This account is tracked by balance, so it has no transactions to import.",
        )
    return account


@router.post("/imports/preview")
def preview_import(body: ImportRequest, db: Db) -> csvimport.Preview:
    account = _import_account(db, body.account_id)
    try:
        return csvimport.preview(db, account, body.content, body.mapping)
    except csvimport.ImportProblem as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/imports", status_code=status.HTTP_201_CREATED)
def run_import(body: ImportRequest, db: Db) -> csvimport.ImportResult:
    account = _import_account(db, body.account_id)
    if body.mapping is None:
        raise HTTPException(422, "Preview first to get a mapping.")
    try:
        return csvimport.run_import(db, account, body.filename, body.content, body.mapping)
    except csvimport.ImportProblem as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.get("/imports")
def list_imports(db: Db, account_id: int | None = None) -> list[ImportOut]:
    left = (
        select(Transaction.import_id, func.count().label("n"))
        .where(Transaction.import_id.is_not(None))
        .group_by(Transaction.import_id)
        .subquery()
    )
    query = (
        select(ImportBatch, Account.name, func.coalesce(left.c.n, 0))
        .join(Account, ImportBatch.account_id == Account.id)
        .outerjoin(left, left.c.import_id == ImportBatch.id)
        .order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc())
    )
    if account_id is not None:
        query = query.where(ImportBatch.account_id == account_id)
    return [
        ImportOut(
            id=b.id,
            account_id=b.account_id,
            account=name,
            filename=b.filename,
            rows_added=b.rows_added,
            rows_duplicate=b.rows_duplicate,
            rows_skipped=b.rows_skipped,
            created_at=b.created_at,
            transactions_left=int(n),
        )
        for b, name, n in db.execute(query).all()
    ]


class UndoResult(BaseModel):
    transactions_removed: int


@router.delete("/imports/{import_id}")
def undo_import(import_id: int, db: Db) -> UndoResult:
    batch = _get(db, ImportBatch, import_id, "import")
    txns = list(db.scalars(select(Transaction).where(Transaction.import_id == batch.id)))
    for t in txns:
        db.delete(t)
    db.delete(batch)
    db.commit()
    return UndoResult(transactions_removed=len(txns))


# --- dashboard, backup, demo ----------------------------------------------------------------


@router.get("/dashboard")
def get_dashboard(
    request: Request, db: Db, months: Annotated[int, Query(ge=2, le=36)] = 12
) -> balances.Dashboard:
    return balances.dashboard(db, today(request), months)


@router.get("/export", response_model=None)
def export_backup(request: Request, db: Db) -> JSONResponse:
    name = f"hisaab-backup-{today(request).isoformat()}.json"
    return JSONResponse(
        export_all(db), headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


class DemoResult(BaseModel):
    accounts: int
    transactions: int
    snapshots: int


@router.post("/demo", status_code=status.HTTP_201_CREATED)
def demo_load(request: Request, db: Db) -> DemoResult:
    if not ledger_is_empty(db):
        raise HTTPException(status.HTTP_409_CONFLICT, "Demo data only loads into an empty ledger.")
    return DemoResult(**load_demo(db, today(request)))


class DemoRemoved(BaseModel):
    accounts_removed: int


@router.delete("/demo")
def demo_remove(db: Db) -> DemoRemoved:
    return DemoRemoved(accounts_removed=remove_demo(db))
