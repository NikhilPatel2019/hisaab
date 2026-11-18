"""Investments HTTP API: your portfolio, CAS imports, price updates."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hisaab.api import require_user
from hisaab.db import get_session
from hisaab.docs.api import RunnerState, optional_box, runner_of, vault_of
from hisaab.invest import jobs
from hisaab.invest.importer import pending
from hisaab.invest.num import e6_str
from hisaab.invest.prices import STALE_AFTER_DAYS
from hisaab.invest.valuation import positions, xirr_for
from hisaab.models import CasImport, Document, Holding, InvestmentTxn

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])
Db = Annotated[Session, Depends(get_session)]

AssetClass = Literal["equity", "debt", "hybrid", "gold", "other"]
CLASS_LABELS: dict[str, str] = {
    "equity": "Equity",
    "debt": "Debt",
    "hybrid": "Hybrid",
    "gold": "Gold & silver",
    "other": "Other",
}


def today(request: Request) -> date:
    return datetime.now(ZoneInfo(request.app.state.settings.timezone)).date()


class HoldingOut(BaseModel):
    account_id: int
    account_name: str
    instrument_id: int
    isin: str
    name: str
    kind: str
    asset_class: AssetClass
    folio: str | None  # last digits only, e.g. "••7890"
    units: str
    price: str
    price_date: date
    price_source: str
    stale: bool
    statement_date: date
    value_paise: int
    cost_paise: int | None
    gain_paise: int | None
    xirr_pct: float | None


class MixOut(BaseModel):
    key: AssetClass
    label: str
    value_paise: int


class HoldingsAccountOut(BaseModel):
    id: int
    name: str
    value_paise: int
    statement_date: date


class ImportNote(BaseModel):
    document_id: int
    filename: str
    period_end: date | None
    status: Literal["ok", "warnings", "failed"]
    message: str | None
    warnings: list[str]


class LastRun(BaseModel):
    at: datetime
    text: str
    errors: list[str] = []


class Portfolio(BaseModel):
    as_of: date
    total_paise: int
    cost_paise: int | None  # of the holdings whose cost the statements give
    gain_paise: int | None
    gain_pct: float | None
    xirr_pct: float | None  # from fund transactions in detailed CAS
    xirr_covers_paise: int  # how much of the total the XIRR is about
    prices_as_of: date | None
    statement_date: date | None
    stale: int
    mix: list[MixOut]
    accounts: list[HoldingsAccountOut]
    holdings: list[HoldingOut]
    pending_imports: int
    problems: list[ImportNote]
    last_prices: LastRun | None
    running: bool


def _pct(x: float | None) -> float | None:
    return None if x is None else round(x * 100, 2)


def _folio_mask(tag: str) -> str | None:
    tail = tag.split(":", 1)[0]
    return f"••{tail}" if tail else None


def _last(db: Session, key: str) -> LastRun | None:
    data = jobs.recall(db, key)
    if not data:
        return None
    try:
        return LastRun(at=data["at"], text=data.get("text", ""), errors=data.get("errors", []))
    except (KeyError, ValueError):
        return None


@router.get("/investments")
def get_portfolio(request: Request, db: Db, account_id: int | None = None) -> Portfolio:
    on = today(request)
    ps = positions(db, on, account_id)
    account_ids = {p.account.id for p in ps}
    txns_by: dict[tuple[int, int, str], list[InvestmentTxn]] = defaultdict(list)
    if account_ids:
        for t in db.scalars(select(InvestmentTxn).where(InvestmentTxn.account_id.in_(account_ids))):
            txns_by[(t.account_id, t.instrument_id, t.folio)].append(t)

    rows: list[HoldingOut] = []
    all_txns: list[InvestmentTxn] = []
    covered = 0
    for p in ps:
        key = (p.account.id, p.instrument.id, p.folio)
        own = txns_by.get(key, [])
        rate = xirr_for(own, p.value_paise, on) if own else None
        if own:
            all_txns += own
            covered += p.value_paise
        stale = (on - p.price_date).days > STALE_AFTER_DAYS.get(p.instrument.kind, 40)
        rows.append(
            HoldingOut(
                account_id=p.account.id,
                account_name=p.account.name,
                instrument_id=p.instrument.id,
                isin=p.instrument.isin,
                name=p.instrument.name,
                kind=p.instrument.kind,
                asset_class=p.instrument.asset_class,  # type: ignore[arg-type]
                folio=_folio_mask(p.folio),
                units=e6_str(p.units_e6) or "0",
                price=e6_str(p.price_e6) or "0",
                price_date=p.price_date,
                price_source=p.price_source,
                stale=stale,
                statement_date=p.as_of,
                value_paise=p.value_paise,
                cost_paise=p.cost_paise,
                gain_paise=None if p.cost_paise is None else p.value_paise - p.cost_paise,
                xirr_pct=_pct(rate),
            )
        )
    rows.sort(key=lambda r: -r.value_paise)

    # Funds you've fully sold still count towards the overall XIRR.
    held_keys = {(p.account.id, p.instrument.id, p.folio) for p in ps}
    for k, ts in txns_by.items():
        if k not in held_keys:
            all_txns += ts
    total = sum(r.value_paise for r in rows)
    with_cost = [r for r in rows if r.cost_paise is not None]
    cost = sum(r.cost_paise or 0 for r in with_cost) if with_cost else None
    gain = sum(r.gain_paise or 0 for r in with_cost) if with_cost else None
    mix: dict[str, int] = defaultdict(int)
    for r in rows:
        mix[r.asset_class] += r.value_paise
    by_account: dict[int, HoldingsAccountOut] = {}
    for p in ps:
        a = by_account.setdefault(
            p.account.id,
            HoldingsAccountOut(
                id=p.account.id, name=p.account.name, value_paise=0, statement_date=p.as_of
            ),
        )
        a.value_paise += p.value_paise
    market = [r.price_date for r in rows if r.price_source != "cas"]

    problems = []
    q = (
        select(CasImport, Document.filename)
        .join(Document, Document.id == CasImport.document_id)
        .where(CasImport.status != "ok")
        .order_by(CasImport.period_end.desc(), CasImport.id.desc())
        .limit(20)
    )
    for imp, filename in db.execute(q).all():
        problems.append(
            ImportNote(
                document_id=imp.document_id,
                filename=filename,
                period_end=imp.period_end,
                status=imp.status,  # type: ignore[arg-type]
                message=imp.message,
                warnings=json.loads(imp.warnings or "[]"),
            )
        )
    return Portfolio(
        as_of=on,
        total_paise=total,
        cost_paise=cost,
        gain_paise=gain,
        gain_pct=_pct(gain / cost) if cost and gain is not None else None,
        xirr_pct=_pct(xirr_for(all_txns, covered, on)) if all_txns else None,
        xirr_covers_paise=covered,
        prices_as_of=max(market) if market else None,
        statement_date=db.scalar(select(func.max(Holding.as_of))),
        stale=sum(1 for r in rows if r.stale),
        mix=[
            MixOut(key=k, label=CLASS_LABELS[k], value_paise=v)  # type: ignore[arg-type]
            for k, v in sorted(mix.items(), key=lambda kv: -kv[1])
        ],
        accounts=sorted(by_account.values(), key=lambda a: -a.value_paise),
        holdings=rows,
        pending_imports=len(pending(db)),
        problems=problems,
        last_prices=_last(db, jobs.LAST_PRICES),
        running=runner_of(request).running,
    )


def _start(request: Request, label: str, fn: object, wait: bool) -> RunnerState:
    runner = runner_of(request)
    if not runner.start_job(label, fn, wait=wait):  # type: ignore[arg-type]
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Something else is running. Try again in a minute."
        )
    return RunnerState(running=runner.running, current=runner.current, started_at=runner.started_at)


@router.post("/investments/import", status_code=status.HTTP_202_ACCEPTED)
def import_cas(request: Request, retry_failed: bool = False, wait: bool = False) -> RunnerState:
    """Read every CAS in the postbox that hasn't been read yet."""
    factory, vault, box, on = (
        request.app.state.sessionmaker,
        vault_of(request),
        optional_box(request),
        today(request),
    )
    return _start(
        request,
        "Reading CAS files",
        lambda: jobs.import_cas(factory, vault, box, on, retry_failed=retry_failed),
        wait,
    )


@router.post("/investments/prices", status_code=status.HTTP_202_ACCEPTED)
def refresh_prices(request: Request, wait: bool = False) -> RunnerState:
    """Fetch today's NAVs and closing prices, then revalue your holdings."""
    factory, on = request.app.state.sessionmaker, today(request)
    http = request.app.state.price_http
    return _start(request, "Updating prices", lambda: jobs.refresh_prices(factory, on, http), wait)


@router.post("/investments/backfill", status_code=status.HTTP_202_ACCEPTED)
def backfill(request: Request, months: int = 24, wait: bool = False) -> RunnerState:
    """Work out month-end values from fund transactions and past NAVs."""
    if not 1 <= months <= 120:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Months should be between 1 and 120."
        )
    factory, on = request.app.state.sessionmaker, today(request)
    http = request.app.state.price_http
    return _start(
        request, "Filling in history", lambda: jobs.backfill(factory, on, months, http), wait
    )
