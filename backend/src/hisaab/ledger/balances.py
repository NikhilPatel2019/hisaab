"""Balances, net worth over time, cashflow and this month's pace.

Written with plain SQL aggregates that behave the same on SQLite, PostgreSQL and MySQL.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from pydantic import BaseModel
from sqlalchemy import ColumnElement, and_, case, extract, func, select
from sqlalchemy.orm import Session

from hisaab.models import Account, BalanceSnapshot, Category, Transaction

GROUPS: list[tuple[str, str, tuple[str, ...]]] = [
    ("cash", "Bank & cash", ("bank", "cash", "wallet")),
    ("deposits", "Deposits", ("deposit",)),
    ("investments", "Investments", ("investment",)),
    ("retirement", "Retirement", ("retirement",)),
    ("property", "Property & other", ("property", "other_asset")),
    ("cards", "Credit cards", ("credit_card",)),
    ("loans", "Loans", ("loan", "other_liability")),
]
GROUP_OF = {kind: key for key, _, kinds in GROUPS for kind in kinds}


def month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def balances_on(session: Session, on: date) -> dict[int, int]:
    """Every account's balance at the end of `on`, in paise."""
    accounts = list(session.scalars(select(Account)))
    sums = dict(
        session.execute(
            select(Transaction.account_id, func.coalesce(func.sum(Transaction.amount_paise), 0))
            .where(Transaction.posted_on <= on)
            .group_by(Transaction.account_id)
        ).all()
    )
    latest = (
        select(BalanceSnapshot.account_id, func.max(BalanceSnapshot.as_of).label("as_of"))
        .where(BalanceSnapshot.as_of <= on)
        .group_by(BalanceSnapshot.account_id)
        .subquery()
    )
    snaps = dict(
        session.execute(
            select(BalanceSnapshot.account_id, BalanceSnapshot.balance_paise).join(
                latest,
                and_(
                    BalanceSnapshot.account_id == latest.c.account_id,
                    BalanceSnapshot.as_of == latest.c.as_of,
                ),
            )
        ).all()
    )
    out: dict[int, int] = {}
    for a in accounts:
        if a.tracking == "balance":
            out[a.id] = int(snaps.get(a.id, 0))
        else:
            out[a.id] = int(a.opening_balance_paise + sums.get(a.id, 0))
    return out


def ledger_balance(session: Session, account: Account, on: date) -> int:
    total = session.scalar(
        select(func.coalesce(func.sum(Transaction.amount_paise), 0)).where(
            Transaction.account_id == account.id, Transaction.posted_on <= on
        )
    )
    return int(account.opening_balance_paise + (total or 0))


# --- dashboard ------------------------------------------------------------------------------


class Point(BaseModel):
    on: date
    net_worth_paise: int


class GroupTotal(BaseModel):
    key: str
    label: str
    total_paise: int
    accounts: int


class MonthFlow(BaseModel):
    month: str  # "2026-10"
    income_paise: int
    spent_paise: int
    invested_paise: int


class DayPace(BaseModel):
    day: int
    this_month_paise: int | None
    last_month_paise: int | None


class CategorySpend(BaseModel):
    category_id: int | None
    name: str
    spent_paise: int


class StaleAccount(BaseModel):
    id: int
    name: str
    last_update: date | None


class RecentTxn(BaseModel):
    id: int
    posted_on: date
    account: str
    description: str
    payee: str | None
    category: str | None
    amount_paise: int


class Dashboard(BaseModel):
    as_of: date
    net_worth_paise: int
    change_this_month_paise: int
    groups: list[GroupTotal]
    series: list[Point]
    cashflow: list[MonthFlow]
    month_spent_paise: int
    last_month_same_day_paise: int
    pace: list[DayPace]
    top_categories: list[CategorySpend]
    uncategorised: int
    stale_accounts: list[StaleAccount]
    recent: list[RecentTxn]
    has_data: bool


def _spend_expr() -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    """Spending = money out on expense categories (net of refunds) plus uncategorised debits."""
    is_expense = Category.kind == "expense"
    uncategorised_out = and_(Transaction.category_id.is_(None), Transaction.amount_paise < 0)
    return is_expense, uncategorised_out


def cashflow(session: Session, start: date, end: date) -> list[MonthFlow]:
    y = extract("year", Transaction.posted_on)
    m = extract("month", Transaction.posted_on)
    is_expense, uncategorised_out = _spend_expr()
    rows = session.execute(
        select(
            y,
            m,
            func.coalesce(
                func.sum(case((Category.kind == "income", Transaction.amount_paise), else_=0)), 0
            ),
            func.coalesce(
                func.sum(
                    case(
                        (is_expense, Transaction.amount_paise),
                        (uncategorised_out, Transaction.amount_paise),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(case((Category.kind == "investment", Transaction.amount_paise), else_=0)),
                0,
            ),
        )
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(Transaction.posted_on >= start, Transaction.posted_on <= end)
        .group_by(y, m)
    ).all()
    found = {(int(r[0]), int(r[1])): r for r in rows}
    out = []
    cursor = start.replace(day=1)
    while cursor <= end:
        r = found.get((cursor.year, cursor.month))
        out.append(
            MonthFlow(
                month=f"{cursor.year}-{cursor.month:02d}",
                income_paise=int(r[2]) if r else 0,
                spent_paise=-int(r[3]) if r else 0,
                invested_paise=-int(r[4]) if r else 0,
            )
        )
        cursor = add_months(cursor, 1)
    return out


def _daily_spend(session: Session, start: date, end: date) -> dict[date, int]:
    is_expense, uncategorised_out = _spend_expr()
    rows = session.execute(
        select(Transaction.posted_on, func.sum(Transaction.amount_paise))
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.posted_on >= start,
            Transaction.posted_on <= end,
            (is_expense) | (uncategorised_out),
        )
        .group_by(Transaction.posted_on)
    ).all()
    return {r[0]: -int(r[1]) for r in rows}


def dashboard(session: Session, today: date, months: int = 12) -> Dashboard:
    accounts = list(session.scalars(select(Account)))
    now = balances_on(session, today)
    net = sum(now.values())

    month_start = today.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    before = sum(balances_on(session, last_month_end).values())

    groups = []
    for key, label, kinds in GROUPS:
        members = [a for a in accounts if a.kind in kinds and not (a.archived and not now[a.id])]
        if members:
            groups.append(
                GroupTotal(
                    key=key,
                    label=label,
                    total_paise=sum(now[a.id] for a in members),
                    accounts=len(members),
                )
            )

    points = []
    for i in range(months - 1, 0, -1):
        d = month_end(add_months(month_start, -i))
        points.append(Point(on=d, net_worth_paise=sum(balances_on(session, d).values())))
    points.append(Point(on=today, net_worth_paise=net))

    flows = cashflow(session, add_months(month_start, -(min(months, 12) - 1)), today)

    last_month_start = last_month_end.replace(day=1)
    daily = _daily_spend(session, last_month_start, today)
    pace = []
    this_total = last_total = 0
    last_days = last_month_end.day
    for day in range(1, max(today.day, last_days) + 1):
        this_val = last_val = None
        if day <= today.day:
            this_total += daily.get(month_start.replace(day=day), 0)
            this_val = this_total
        if day <= last_days:
            last_total += daily.get(last_month_start.replace(day=day), 0)
            last_val = last_total
        pace.append(DayPace(day=day, this_month_paise=this_val, last_month_paise=last_val))
    same_day = (
        next((p.last_month_paise for p in pace if p.day == min(today.day, last_days)), 0) or 0
    )

    is_expense, uncategorised_out = _spend_expr()
    top = session.execute(
        select(Category.id, Category.name, func.sum(Transaction.amount_paise))
        .select_from(Transaction)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(Transaction.posted_on >= month_start, Transaction.posted_on <= today)
        .where((is_expense) | (uncategorised_out))
        .group_by(Category.id, Category.name)
    ).all()
    top_categories = sorted(
        (
            CategorySpend(category_id=r[0], name=r[1] or "Not sorted yet", spent_paise=-int(r[2]))
            for r in top
            if r[2]
        ),
        key=lambda c: -c.spent_paise,
    )[:6]

    uncategorised = session.scalar(
        select(func.count())
        .select_from(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .where(Transaction.category_id.is_(None), Account.tracking == "transactions")
    )

    last_snap = dict(
        session.execute(
            select(BalanceSnapshot.account_id, func.max(BalanceSnapshot.as_of)).group_by(
                BalanceSnapshot.account_id
            )
        ).all()
    )
    stale = [
        StaleAccount(id=a.id, name=a.name, last_update=last_snap.get(a.id))
        for a in accounts
        if a.tracking == "balance"
        and not a.archived
        and (last_snap.get(a.id) is None or (today - last_snap[a.id]).days > 45)
    ]

    recent_rows = session.execute(
        select(Transaction, Account.name, Category.name)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(Transaction.posted_on <= today)
        .order_by(Transaction.posted_on.desc(), Transaction.id.desc())
        .limit(6)
    ).all()
    recent = [
        RecentTxn(
            id=t.id,
            posted_on=t.posted_on,
            account=acct,
            description=t.description,
            payee=t.payee,
            category=cat,
            amount_paise=t.amount_paise,
        )
        for t, acct, cat in recent_rows
    ]

    return Dashboard(
        as_of=today,
        net_worth_paise=net,
        change_this_month_paise=net - before,
        groups=groups,
        series=points,
        cashflow=flows,
        month_spent_paise=this_total,
        last_month_same_day_paise=same_day,
        pace=pace,
        top_categories=top_categories,
        uncategorised=int(uncategorised or 0),
        stale_accounts=stale,
        recent=recent,
        has_data=bool(accounts),
    )
