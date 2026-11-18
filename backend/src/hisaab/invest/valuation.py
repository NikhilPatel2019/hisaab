"""What your holdings are worth: on any day, at the latest price on or before it.

Values are written as balance snapshots on each holdings account, so net worth, the home
chart and backups need nothing special: a holdings account is just an account whose value
we work out instead of you typing it.
"""

from __future__ import annotations

import calendar
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from hisaab.invest import xirr as xirr_mod
from hisaab.invest.num import value_paise
from hisaab.ledger.balances import add_months
from hisaab.models import Account, BalanceSnapshot, Holding, Instrument, InvestmentTxn, Price

log = logging.getLogger("hisaab.invest")

# Snapshots we may overwrite with a computed value. A statement's own figure ("cas") and one
# you typed ("manual") always win for their day.
COMPUTED = ("valuation", "backfill")


@dataclass
class Position:
    account: Account
    instrument: Instrument
    folio: str
    as_of: date  # the statement it comes from
    units_e6: int
    price_e6: int
    price_date: date
    price_source: str
    value_paise: int
    cost_paise: int | None


def holdings_accounts(session: Session) -> list[Account]:
    return list(
        session.scalars(
            select(Account)
            .where(Account.tracking == "holdings", Account.archived.is_(False))
            .order_by(Account.id)
        )
    )


def latest_holdings(session: Session, account_id: int, on: date) -> list[Holding]:
    as_of = session.scalar(
        select(func.max(Holding.as_of)).where(Holding.account_id == account_id, Holding.as_of <= on)
    )
    if as_of is None:
        return []
    return list(
        session.scalars(
            select(Holding).where(Holding.account_id == account_id, Holding.as_of == as_of)
        )
    )


def prices_on(session: Session, instrument_ids: list[int], on: date) -> dict[int, Price]:
    """The latest price on or before `on` for each instrument."""
    if not instrument_ids:
        return {}
    latest = (
        select(Price.instrument_id, func.max(Price.price_date).label("d"))
        .where(Price.instrument_id.in_(instrument_ids), Price.price_date <= on)
        .group_by(Price.instrument_id)
        .subquery()
    )
    rows = session.scalars(
        select(Price).join(
            latest,
            and_(Price.instrument_id == latest.c.instrument_id, Price.price_date == latest.c.d),
        )
    )
    return {p.instrument_id: p for p in rows}


def positions(session: Session, on: date, account_id: int | None = None) -> list[Position]:
    out: list[Position] = []
    accounts = holdings_accounts(session)
    if account_id is not None:
        accounts = [a for a in accounts if a.id == account_id]
    for acc in accounts:
        hs = latest_holdings(session, acc.id, on)
        if not hs:
            continue
        insts = {
            i.id: i
            for i in session.scalars(
                select(Instrument).where(Instrument.id.in_([h.instrument_id for h in hs]))
            )
        }
        px = prices_on(session, list(insts), on)
        for h in hs:
            p = px.get(h.instrument_id)
            # On its own date a statement's price and value are the truth, to the paisa;
            # they hold until a newer price comes in.
            if p is None or p.price_date <= h.as_of:
                price, pdate, src, value = h.price_e6, h.as_of, "cas", h.value_paise
            else:
                price, pdate, src = p.price_e6, p.price_date, p.source
                value = value_paise(h.units_e6, price)
            out.append(
                Position(
                    acc,
                    insts[h.instrument_id],
                    h.folio,
                    h.as_of,
                    h.units_e6,
                    price,
                    pdate,
                    src,
                    value,
                    h.cost_paise,
                )
            )
    return out


def account_value(session: Session, account: Account, on: date) -> int | None:
    ps = positions(session, on, account.id)
    return sum(p.value_paise for p in ps) if ps else None


def _put_snapshot(session: Session, account_id: int, on: date, value: int, source: str) -> bool:
    snap = session.scalar(
        select(BalanceSnapshot).where(
            BalanceSnapshot.account_id == account_id, BalanceSnapshot.as_of == on
        )
    )
    if snap is None:
        session.add(
            BalanceSnapshot(account_id=account_id, as_of=on, balance_paise=value, source=source)
        )
        return True
    if snap.source in COMPUTED and (snap.balance_paise != value or snap.source != source):
        snap.balance_paise, snap.source = value, source
        return True
    return False


def revalue(session: Session, on: date) -> int:
    """Write today's value of every holdings account. Returns how many changed."""
    changed = 0
    for acc in holdings_accounts(session):
        value = account_value(session, acc, on)
        if value is not None and _put_snapshot(session, acc.id, on, value, "valuation"):
            changed += 1
    session.commit()
    return changed


# --- returns ----------------------------------------------------------------------------------

# Rows that move money between you and the fund. Taxes are costs; reinvested dividends,
# segregated units and gifts move no money.
_TAX = ("STT_TAX", "STAMP_DUTY_TAX", "TDS_TAX")
_NO_CASH = ("DIVIDEND_REINVEST", "SEGREGATION", "GIFT_IN", "GIFT_OUT", "MISC", "UNKNOWN")


def cash_flow_paise(t: InvestmentTxn) -> int:
    """Signed from your pocket's point of view: money in is negative, money back positive."""
    amount = abs(t.amount_paise or 0)
    if not amount or t.type in _NO_CASH:
        return 0
    if t.type in _TAX:
        return -amount
    if t.type == "DIVIDEND_PAYOUT":
        return amount
    units = t.units_e6 or 0
    if units > 0:
        return -amount
    if units < 0:
        return amount
    return 0


def xirr_for(txns: list[InvestmentTxn], value_now: int, on: date) -> float | None:
    flows = [(t.txn_date, cash_flow_paise(t) / 100) for t in txns]
    flows = [f for f in flows if f[1]]
    if not flows:
        return None
    flows.append((on, value_now / 100))
    return xirr_mod.xirr(flows)


# --- history ----------------------------------------------------------------------------------


def month_ends(start: date, end: date) -> list[date]:
    out = []
    d = start.replace(day=calendar.monthrange(start.year, start.month)[1])
    while d <= end:
        out.append(d)
        nxt = d + timedelta(days=1)
        d = nxt.replace(day=calendar.monthrange(nxt.year, nxt.month)[1])
    return out


def units_by_date(txns: list[InvestmentTxn]) -> dict[tuple[int, str], list[tuple[date, int]]]:
    """Running units per (instrument, folio), from each row's printed balance or the sum."""
    series: dict[tuple[int, str], list[tuple[date, int]]] = defaultdict(list)
    running: dict[tuple[int, str], int] = defaultdict(int)
    for t in sorted(txns, key=lambda x: (x.txn_date, x.id)):
        k = (t.instrument_id, t.folio)
        if t.units_e6 is None and t.balance_e6 is None:
            continue
        running[k] = t.balance_e6 if t.balance_e6 is not None else running[k] + (t.units_e6 or 0)
        series[k].append((t.txn_date, running[k]))
    return series


def _units_on(points: list[tuple[date, int]], on: date) -> int:
    units = 0
    for d, u in points:
        if d > on:
            break
        units = u
    return units


def backfill_account(
    session: Session,
    account: Account,
    history: dict[int, list[tuple[date, int]]],
    months: int,
    today: date,
) -> int:
    """Month-end values from fund transactions, for the months before your first statement.
    `history` is each instrument's prices as [(date, price_e6)], oldest first."""
    txns = list(
        session.scalars(select(InvestmentTxn).where(InvestmentTxn.account_id == account.id))
    )
    if not txns:
        return 0
    series = units_by_date(txns)
    first = min(t.txn_date for t in txns)
    start = max(first, add_months(today.replace(day=1), -months))
    written = 0
    for me in month_ends(start, today - timedelta(days=1)):
        total = 0
        complete = True
        for (inst_id, _folio), points in series.items():
            units = _units_on(points, me)
            if units == 0:
                continue
            prices = [p for d, p in history.get(inst_id, []) if d <= me]
            if not prices:
                complete = False
                break
            total += value_paise(units, prices[-1])
        if complete and _put_snapshot(session, account.id, me, total, "backfill"):
            written += 1
    session.commit()
    return written
