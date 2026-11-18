"""Filling in net-worth history from fund transactions and past NAVs."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from hisaab.invest.importer import upsert_price
from hisaab.invest.num import to_e6
from hisaab.invest.prices import Http, PriceError, fetch_mfapi
from hisaab.invest.valuation import backfill_account, holdings_accounts, month_ends
from hisaab.models import Instrument, InvestmentTxn, Price


def backfill(session: Session, http: Http, today: date, months: int = 24) -> dict[str, object]:
    """Month-end values for every holdings account that has fund transactions."""
    written = 0
    errors: list[str] = []
    for acc in holdings_accounts(session):
        inst_ids = set(
            session.scalars(
                select(InvestmentTxn.instrument_id).where(InvestmentTxn.account_id == acc.id)
            )
        )
        if not inst_ids:
            continue
        history: dict[int, list[tuple[date, int]]] = defaultdict(list)
        for inst in session.scalars(select(Instrument).where(Instrument.id.in_(inst_ids))):
            navs: list[tuple[date, int]] = []
            if inst.amfi_code:
                try:
                    fetched = fetch_mfapi(http, inst.amfi_code)
                except PriceError as exc:
                    errors.append(f"{inst.name}: {exc}")
                    fetched = []
                # Keep one NAV per month (the last on or before each month end), plus the newest.
                if fetched:
                    ends = month_ends(fetched[0][0], today)
                    j, last = 0, None
                    for me in ends:
                        while j < len(fetched) and fetched[j][0] <= me:
                            last = fetched[j]
                            j += 1
                        if last:
                            upsert_price(session, inst.id, last[0], to_e6(last[1]) or 0, "mfapi")
            stored = session.execute(
                select(Price.price_date, Price.price_e6)
                .where(Price.instrument_id == inst.id)
                .order_by(Price.price_date)
            ).all()
            navs = [(d, p) for d, p in stored]
            history[inst.id] = navs
        session.flush()
        written += backfill_account(session, acc, history, months, today)
    session.commit()
    return {"snapshots": written, "errors": errors}
