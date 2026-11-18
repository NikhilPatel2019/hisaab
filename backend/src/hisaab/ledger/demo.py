# ruff: noqa: E501  (data-heavy: one line per invented transaction pattern)
"""A fictional household to look around with: "Aarav", 18 months, every shop invented.

Deterministic (same seed → same data, shifted to end today), so screenshots and tests are
stable. Everything it creates is marked as demo and can be removed in one go.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from hisaab.ledger.balances import add_months
from hisaab.models import (
    E6,
    Account,
    BalanceSnapshot,
    Category,
    Holding,
    Instrument,
    InvestmentTxn,
    Price,
    Transaction,
)

MONTHS = 18


@dataclass
class _Txn:
    account: str
    on: date
    paise: int
    description: str
    payee: str | None
    category: str | None


def _rupees(r: float) -> int:
    return round(r * 100)


def remove_demo(session: Session) -> int:
    ids = list(session.scalars(select(Account.id).where(Account.is_demo.is_(True))))
    if ids:
        session.execute(delete(Transaction).where(Transaction.account_id.in_(ids)))
        session.execute(delete(BalanceSnapshot).where(BalanceSnapshot.account_id.in_(ids)))
        session.execute(delete(Account).where(Account.id.in_(ids)))
    session.execute(delete(Instrument).where(Instrument.isin.like("DEMO%")))
    session.commit()
    return len(ids)


def load_demo(session: Session, today: date, seed: int = 2026) -> dict[str, int]:
    rng = random.Random(seed)  # noqa: S311 - fake data, not security
    cats = {c.name: c.id for c in session.scalars(select(Category))}
    start = add_months(today.replace(day=1), -(MONTHS - 1))

    specs = {
        "salary": ("Salary account", "bank", "transactions", "Demo Bank A", "2041", 60_000),
        "spend": ("Spending account", "bank", "transactions", "Demo Bank B", "9152", 5_000),
        "rewards": ("Rewards card", "credit_card", "transactions", "Demo Bank A", "7731", 0),
        "food": ("Food card", "credit_card", "transactions", "Demo Bank C", "3307", 0),
        "mf": ("Mutual funds", "investment", "holdings", "Via CAS", None, 0),
        "shares": ("Shares", "investment", "holdings", "Demo Broker", None, 0),
        "epf": ("EPF", "retirement", "balance", "EPFO", None, 0),
        "fd": ("Fixed deposit", "deposit", "balance", "Demo Bank A", None, 0),
    }
    accounts: dict[str, Account] = {}
    for key, (name, kind, tracking, inst, mask, opening) in specs.items():
        a = Account(
            name=name,
            kind=kind,
            tracking=tracking,
            institution=inst,
            mask=mask,
            opening_balance_paise=_rupees(opening),
            is_demo=True,
        )
        session.add(a)
        accounts[key] = a
    session.flush()

    txns: list[_Txn] = []

    def add(
        acct: str, on: date, rupees: float, desc: str, payee: str | None, cat: str | None
    ) -> None:
        if on <= today:
            txns.append(_Txn(acct, on, _rupees(rupees), desc, payee, cat))

    def day(month: date, d: int) -> date:
        return month.replace(day=min(d, 28))

    card_spend: dict[tuple[str, str], float] = {}
    salary = 1_20_000.0
    mf_value, shares_value, epf_value, fd_value = 0.0, 0.0, 2_10_000.0, 0.0
    snapshots: list[tuple[str, date, float]] = []
    growth: list[tuple[date, date, float]] = []  # (SIP day, month end, the funds' growth)

    for i in range(MONTHS):
        month = add_months(start, i)
        if month.month == 4 and i > 0:
            salary = round(salary * 1.09, -2)
        ym = month.strftime("%Y-%m")
        last_ym = add_months(month, -1).strftime("%Y-%m")

        add(
            "salary",
            day(month, 1),
            salary,
            "NEFT/ACME SOFTWARE PVT LTD/SALARY",
            "Acme Software",
            "Salary",
        )
        add(
            "salary",
            day(month, 2),
            -20_000,
            "UPI/OWN/SPENDING ACCOUNT/TRANSFER",
            "Spending account",
            "Own transfer",
        )
        add(
            "spend",
            day(month, 2),
            20_000,
            "UPI/OWN/SALARY ACCOUNT/TRANSFER",
            "Salary account",
            "Own transfer",
        )
        add("salary", day(month, 3), -18_000, "UPI/R MEHTA/LANDLORD/RENT", "R Mehta", "Rent")
        add("salary", day(month, 5), -25_000, "ACH/MF SIP/4 FUNDS", "SIP", "SIP & mutual funds")
        add(
            "salary",
            day(month, 10),
            -8_000,
            "UPI/DEMO BROKER/FUNDS",
            "Demo Broker",
            "Shares & broker",
        )
        if month.month in (3, 6, 9, 12):
            add(
                "salary",
                day(month, 28),
                rng.uniform(900, 1300),
                "INT CREDIT SAVINGS",
                "Bank interest",
                "Interest",
            )

        for acct, pay_day in (("rewards", 15), ("food", 20)):
            due = card_spend.get((acct, last_ym), 0.0)
            if due:
                label = "Rewards card" if acct == "rewards" else "Food card"
                add(
                    "salary",
                    day(month, pay_day),
                    -due,
                    f"UPI/CARD BILL/{label.upper()}",
                    label,
                    "Card bill payment",
                )
                add(
                    acct,
                    day(month, pay_day),
                    due,
                    "PAYMENT RECEIVED - THANK YOU",
                    None,
                    "Card bill payment",
                )

        # Everyday spending from the spending account.
        for _ in range(rng.randint(4, 6)):
            add(
                "spend",
                day(month, rng.randint(1, 28)),
                -rng.uniform(300, 2400),
                "UPI/GREENCART GROCERS/YESB",
                "GreenCart Grocers",
                "Groceries",
            )
        for _ in range(rng.randint(10, 15)):
            add(
                "spend",
                day(month, rng.randint(1, 28)),
                -rng.uniform(40, 260),
                "UPI/CORNER CHAI STALL/PAYTM",
                "Corner Chai Stall",
                "Snacks & drinks",
            )
        for _ in range(rng.randint(6, 10)):
            add(
                "spend",
                day(month, rng.randint(1, 28)),
                -rng.uniform(60, 380),
                "UPI/CITY METRO/RECHARGE",
                "City Metro",
                "Local transport",
            )
        add(
            "spend",
            day(month, 8),
            -399,
            "UPI/TALKMORE MOBILE/RECHARGE",
            "TalkMore Mobile",
            "Bills & recharges",
        )
        add(
            "spend",
            day(month, 12),
            -799,
            "UPI/FASTNET BROADBAND/BILL",
            "FastNet Broadband",
            "Bills & recharges",
        )
        add(
            "spend",
            day(month, 18),
            -rng.uniform(900, 1900),
            "BILLPAY/CITY POWER/ELECTRICITY",
            "City Power",
            "Electricity & utilities",
        )
        add("spend", day(month, 6), -3_000, "UPI/HOUSE HELP/SALARY", "House help", "Household help")

        # Rewards card: shopping, dining, subscriptions, the odd trip.
        def card(
            acct: str, on: date, rupees: float, desc: str, payee: str, cat: str, key: str = ym
        ) -> None:
            add(acct, on, -rupees, desc, payee, cat)
            if on <= today:
                card_spend[(acct, key)] = card_spend.get((acct, key), 0.0) + rupees

        for _ in range(rng.randint(2, 4)):
            card(
                "rewards",
                day(month, rng.randint(1, 28)),
                rng.uniform(500, 4200),
                "SHOPKART ONLINE",
                "ShopKart",
                "Shopping",
            )
        for _ in range(rng.randint(2, 3)):
            card(
                "rewards",
                day(month, rng.randint(1, 28)),
                rng.uniform(800, 2600),
                "THE BRASS SPOON RESTAURANT",
                "The Brass Spoon",
                "Dining out",
            )
        card("rewards", day(month, 6), 199, "STREAMBOX SUBSCRIPTION", "StreamBox", "Subscriptions")
        card("rewards", day(month, 9), 119, "TUNEWAVE MUSIC", "TuneWave", "Subscriptions")
        card("rewards", day(month, 14), 130, "CLOUDVAULT STORAGE", "CloudVault", "Subscriptions")
        if i >= MONTHS - 8:
            card(
                "rewards",
                day(month, 24),
                1_999,
                "AI ASSISTANT PRO USD",
                "AI Assistant Pro",
                "Subscriptions",
            )
            card(
                "rewards",
                day(month, 24),
                70,
                "FOREIGN CURRENCY MARKUP",
                "Card fee",
                "Fees & charges",
            )
        if i % 4 == 2:
            card(
                "rewards",
                day(month, rng.randint(5, 25)),
                rng.uniform(3_000, 9_000),
                "SKYTRAIL AIRWAYS",
                "SkyTrail Airways",
                "Travel",
            )
        if rng.random() < 0.5:
            card(
                "rewards",
                day(month, rng.randint(1, 28)),
                rng.uniform(300, 1500),
                "WELLNESS PHARMACY",
                "Wellness Pharmacy",
                "Health",
            )
        if month.month in (8, 10, 11):
            card(
                "rewards",
                day(month, rng.randint(1, 28)),
                rng.uniform(1_500, 5_000),
                "GIFTS AND MORE",
                "Gifts and More",
                "Family & gifts",
            )

        # Food card: delivery orders, creeping up in recent months.
        orders = rng.randint(14, 20) + (8 if i >= MONTHS - 2 else 0)
        for _ in range(orders):
            card(
                "food",
                day(month, rng.randint(1, 28)),
                rng.uniform(180, 520),
                "QUICKBITE FOOD ORDER",
                "QuickBite",
                "Food delivery",
            )

        # Balance-tracked accounts: one value per month.
        snap_day = min(month_end_safe(month), today)
        if snap_day >= month:
            g = 1 + rng.gauss(0.016, 0.018)
            mf_value = (mf_value + 25_000) * g
            growth.append((day(month, 5), snap_day, g))
            shares_value = (shares_value + 8_000) * (1 + rng.gauss(0.008, 0.05))
            epf_value = epf_value + 7_800 + epf_value * 0.0825 / 12
            snapshots += [
                ("mf", snap_day, mf_value),
                ("shares", snap_day, shares_value),
                ("epf", snap_day, epf_value),
            ]
            if i == 2:
                fd_value = 1_00_000.0
                add(
                    "salary",
                    day(month, 20),
                    -1_00_000,
                    "FD BOOKED 12M",
                    "Fixed deposit",
                    "Deposits (FD/RD)",
                )
            if fd_value:
                fd_value *= 1 + 0.07 / 12
                snapshots.append(("fd", snap_day, fd_value))

    # A few new shops nobody has sorted yet, so "Needs you" has something to show.
    for k, (name, amt) in enumerate(
        (
            ("FRESH JUICE CORNER", 120),
            ("SANGAM TEXTILES", 1_673),
            ("GOKUL DAIRY", 58),
            ("BOOKNOOK STORE", 449),
        )
    ):
        add("spend", today - timedelta(days=k), -amt, f"UPI/{name}/YESB", name.title(), None)

    for t in txns:
        session.add(
            Transaction(
                account_id=accounts[t.account].id,
                posted_on=t.on,
                amount_paise=t.paise,
                description=t.description,
                payee=t.payee,
                category_id=cats.get(t.category) if t.category else None,
                source="demo",
            )
        )
    # Holdings behind the two investment accounts, so the Investments page has something.
    last_day = growth[-1][1]
    mf_total = _demo_funds(session, accounts["mf"].id, growth)
    shares_total = _demo_shares(session, accounts["shares"].id, last_day, shares_value)
    snapshots = [
        (
            k,
            on,
            (mf_total if k == "mf" else shares_total) / 100
            if on == last_day and k in ("mf", "shares")
            else v,
        )
        for k, on, v in snapshots
    ]
    for key, on, value in snapshots:
        session.add(
            BalanceSnapshot(
                account_id=accounts[key].id, as_of=on, balance_paise=_rupees(value), source="demo"
            )
        )
    session.commit()
    return {"accounts": len(accounts), "transactions": len(txns), "snapshots": len(snapshots)}


def month_end_safe(d: date) -> date:
    nxt = add_months(d.replace(day=1), 1)
    return nxt - timedelta(days=1)


# Fictional instruments: the ISINs start with DEMO so no real price feed ever matches them.
DEMO_FUNDS = (
    ("DEMOF0000001", "Nifty 50 Index Fund (demo)", "equity", 0.35, 150.0),
    ("DEMOF0000002", "Flexi Cap Fund (demo)", "equity", 0.25, 80.0),
    ("DEMOF0000003", "Short Duration Debt Fund (demo)", "debt", 0.25, 30.0),
    ("DEMOF0000004", "Gold Savings Fund (demo)", "gold", 0.15, 20.0),
)
DEMO_SHARES = (
    ("DEMOE0000001", "Stock A (demo)", "equity", 0.40, 1_500.0),
    ("DEMOE0000002", "Stock B (demo)", "equity", 0.35, 820.0),
    ("DEMOE0000003", "Nifty 50 ETF (demo)", "equity", 0.25, 260.0),
)


def _demo_instrument(session: Session, isin: str, name: str, kind: str, asset: str) -> Instrument:
    inst = session.scalar(select(Instrument).where(Instrument.isin == isin))
    if inst is None:
        inst = Instrument(isin=isin, kind=kind, name=name, asset_class=asset)
        session.add(inst)
        session.flush()
    return inst


def _demo_funds(session: Session, account_id: int, growth: list[tuple[date, date, float]]) -> int:
    """Monthly SIPs into four funds whose NAVs follow the account's growth."""
    total = 0
    as_of = growth[-1][1]
    for isin, name, asset, weight, base in DEMO_FUNDS:
        inst = _demo_instrument(session, isin, name, "mf", asset)
        nav, units, cost = base, 0.0, 0
        for n, (sip_day, month_end, g) in enumerate(growth):
            amount = round(25_000 * weight, 2)
            bought = round(amount / nav, 3)
            units = round(units + bought, 3)
            cost += _rupees(amount)
            session.add(
                InvestmentTxn(
                    account_id=account_id,
                    instrument_id=inst.id,
                    folio="",
                    txn_date=sip_day,
                    type="PURCHASE_SIP",
                    description=f"SIP Purchase - Instalment {n + 1}",
                    units_e6=round(bought * E6),
                    price_e6=round(nav * E6),
                    amount_paise=_rupees(amount),
                    balance_e6=round(units * E6),
                    dedupe_hash=f"demo-{isin}-{n}",
                )
            )
            nav = round(nav * g, 4)
            session.add(
                Price(
                    instrument_id=inst.id,
                    price_date=month_end,
                    price_e6=round(nav * E6),
                    source="cas",
                )
            )
        value = round(units * nav * 100)
        total += value
        session.add(
            Holding(
                account_id=account_id,
                instrument_id=inst.id,
                folio="",
                as_of=as_of,
                units_e6=round(units * E6),
                price_e6=round(nav * E6),
                value_paise=value,
                cost_paise=cost,
            )
        )
    return total


def _demo_shares(session: Session, account_id: int, as_of: date, value: float) -> int:
    total = 0
    for isin, name, asset, weight, price in DEMO_SHARES:
        inst = _demo_instrument(session, isin, name, "equity", asset)
        qty = max(1, round(value * weight / price))
        worth = round(qty * price * 100)
        total += worth
        session.add(
            Price(instrument_id=inst.id, price_date=as_of, price_e6=round(price * E6), source="cas")
        )
        session.add(
            Holding(
                account_id=account_id,
                instrument_id=inst.id,
                folio="",
                as_of=as_of,
                units_e6=qty * E6,
                price_e6=round(price * E6),
                value_paise=worth,
                cost_paise=round(8_000 * MONTHS * weight * 100 * 0.85),
            )
        )
    return total
