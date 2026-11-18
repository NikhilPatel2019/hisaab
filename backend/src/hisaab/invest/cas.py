"""Reading a CAS with casparser, turned into hisaab's own shapes.

casparser is pinned (see pyproject.toml): parsers change how they read statements, and a
silent change here would change your holdings. Everything casparser gives us passes through
this module, so the rest of hisaab never depends on its types.

Account numbers, folio numbers and PANs are never kept as they are: accounts are linked by a
one-way hash, and only the last digits are shown.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from hisaab.invest.num import dec

log = logging.getLogger("hisaab.invest")


class CasError(Exception):
    """A CAS couldn't be read. The message is shown as-is, so it never holds secrets."""


@dataclass
class CasHolding:
    isin: str
    name: str
    kind: str  # mf | equity | bond | nps | other
    asset_class: str  # equity | debt | hybrid | gold | other
    units: Decimal
    price: Decimal
    value: Decimal
    cost: Decimal | None = None
    folio: str = ""  # "1234:ab12cd" — last digits and a short hash, never the full folio
    amfi_code: str | None = None
    symbol: str | None = None
    price_date: date | None = None


@dataclass
class CasTxn:
    isin: str
    folio: str
    on: date
    type: str
    description: str
    units: Decimal | None
    price: Decimal | None
    amount: Decimal | None
    balance: Decimal | None
    name: str = ""
    asset_class: str = "other"
    amfi_code: str | None = None


@dataclass
class CasAccount:
    key: str  # stable, hashed: links this to the same hisaab account next month
    kind: str  # demat | mf | nps
    name: str
    institution: str | None
    mask: str | None
    value: Decimal
    holdings: list[CasHolding] = field(default_factory=list)
    txns: list[CasTxn] = field(default_factory=list)


@dataclass
class CasResult:
    file_type: str  # CAMS | KFINTECH | NSDL | CDSL
    cas_type: str | None  # DETAILED | SUMMARY for CAMS/KFintech
    period_start: date | None
    period_end: date
    accounts: list[CasAccount]
    warnings: list[str]


def _hash(*parts: str) -> str:
    return hashlib.sha256("|".join(p.strip().upper() for p in parts).encode()).hexdigest()[:24]


def _digits_tail(value: str | None, n: int = 4) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    return digits[-n:] if len(digits) >= n else None


def folio_tag(folio: str | None) -> str:
    """Turn "12345678 / 90" into "7890:3fa21c": enough to tell folios apart, no more."""
    if not folio or not folio.strip():
        return ""
    return f"{_digits_tail(folio) or ''}:{_hash(folio)[:6]}"


def parse_period_date(text: str | date | None) -> date | None:
    if text is None:
        return None
    if isinstance(text, date):
        return text
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def asset_class_of(kind: str, type_: str | None, name: str | None) -> str:
    t = (type_ or "").upper()
    n = (name or "").upper()
    if "GOLD" in n or "SILVER" in n:
        return "gold"
    if kind == "bond":
        return "debt"
    if kind == "equity":
        if re.search(r"\b(LIQUID|GILT|BOND|G-SEC|GSEC|SDL|TREASURY|MONEY MARKET)\b", n):
            return "debt"
        return "equity"
    if "EQUITY" in t or "ELSS" in t or "INDEX" in t:
        return "equity"
    if any(w in t for w in ("DEBT", "LIQUID", "INCOME", "GILT", "MONEY")):
        return "debt"
    if any(w in t for w in ("HYBRID", "BALANCED", "ARBITRAGE", "ASSET ALLOCATION")):
        return "hybrid"
    if re.search(r"\b(LIQUID|GILT|DEBT|BOND|OVERNIGHT|MONEY MARKET|DURATION|CREDIT RISK)\b", n):
        return "debt"
    if re.search(r"\b(HYBRID|BALANCED|ARBITRAGE|MULTI ASSET)\b", n):
        return "hybrid"
    if re.search(r"\b(EQUITY|INDEX|NIFTY|SENSEX|CAP|FLEXI|ELSS|TAX SAVER|FOCUSED|VALUE)\b", n):
        return "equity"
    return "other"


def _synthetic_isin(prefix: str, *parts: str) -> str:
    """For instruments without an ISIN (an NPS scheme, an unmatched fund)."""
    return f"{prefix}-{_hash(*parts)[:14]}"


def _title(name: str | None) -> str | None:
    if not name:
        return None
    words = re.sub(r"\s+", " ", name).strip()
    return words.title() if words.isupper() else words


# --- casparser → hisaab ---------------------------------------------------------------------


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    return getattr(obj, name, default)


def _from_rta(raw: Any) -> CasResult:
    """CAMS / KFintech CAS: folios of mutual funds, with transactions when DETAILED."""
    period = _attr(raw, "statement_period")
    period_start = parse_period_date(_attr(period, "from_"))
    period_end = parse_period_date(_attr(period, "to"))
    if period_end is None:
        raise CasError("This CAS has no statement date we can read.")
    pans = [f.PAN for f in raw.folios if _attr(f, "PAN")]
    info = _attr(raw, "investor_info")
    who = pans[0] if pans else (_attr(info, "email") or _attr(info, "name") or "investor")
    account = CasAccount(
        key="mf:" + _hash(who),
        kind="mf",
        name="Mutual funds",
        institution="CAMS / KFintech",
        mask=None,
        value=Decimal(0),
    )
    for folio in raw.folios:
        tag = folio_tag(folio.folio)
        for s in folio.schemes:
            isin = s.isin or _synthetic_isin("RTA", s.rta or "", s.rta_code or "", s.scheme)
            v = s.valuation
            units = dec(s.close) or Decimal(0)
            name = re.sub(r"\s+", " ", s.scheme).strip()
            if units != 0:
                h = CasHolding(
                    isin=isin,
                    name=name,
                    kind="mf",
                    asset_class=asset_class_of("mf", s.type, name),
                    units=units,
                    price=dec(v.nav) or Decimal(0),
                    value=dec(v.value) or Decimal(0),
                    cost=dec(v.cost),
                    folio=tag,
                    amfi_code=s.amfi,
                    price_date=parse_period_date(v.date) or period_end,
                )
                account.holdings.append(h)
                account.value += h.value
            for t in s.transactions:
                on = parse_period_date(t.date)
                if on is None:
                    continue
                account.txns.append(
                    CasTxn(
                        isin=isin,
                        folio=tag,
                        on=on,
                        type=str(_attr(t.type, "value", t.type)),
                        description=re.sub(r"\s+", " ", t.description or "").strip()[:200],
                        units=dec(t.units),
                        price=dec(t.nav),
                        amount=dec(t.amount),
                        balance=dec(t.balance),
                        name=name,
                        asset_class=asset_class_of("mf", s.type, name),
                        amfi_code=s.amfi,
                    )
                )
    file_type = str(_attr(raw.file_type, "value", raw.file_type))
    cas_type = str(_attr(raw.cas_type, "value", raw.cas_type))
    return CasResult(
        file_type, cas_type, period_start, period_end, [account], list(raw.parse_warnings or [])
    )


def _from_depository(raw: Any) -> CasResult:
    """NSDL / CDSL CAS: demat accounts, MF folios and NPS, holdings only."""
    period = _attr(raw, "statement_period")
    period_start = parse_period_date(_attr(period, "from_"))
    period_end = parse_period_date(_attr(period, "to"))
    if period_end is None:
        raise CasError("This CAS has no statement date we can read.")
    pans = [o.PAN for a in raw.accounts for o in (a.owners or []) if _attr(o, "PAN")]
    info = _attr(raw, "investor_info")
    who = pans[0] if pans else (_attr(info, "email") or _attr(info, "name") or "investor")
    out: list[CasAccount] = []
    for a in raw.accounts:
        is_mf = (a.type or "").lower().startswith("mutual fund")
        if is_mf:
            acc = CasAccount(
                key="mf:" + _hash(who),
                kind="mf",
                name="Mutual funds",
                institution="MF folios (via CAS)",
                mask=None,
                value=Decimal(0),
            )
        else:
            ident = f"{a.dp_id or ''}{a.client_id or ''}" or a.name
            dp = _title(a.name) or "Demat"
            acc = CasAccount(
                key="demat:" + _hash(ident),
                kind="demat",
                name=f"Demat · {dp}"[:100],
                institution=dp[:100],
                mask=_digits_tail(a.client_id),
                value=Decimal(0),
            )
        for e in a.equities or []:
            units = dec(e.num_shares) or Decimal(0)
            if units == 0:
                continue
            name = _title(e.name) or e.isin
            h = CasHolding(
                isin=e.isin,
                name=name,
                kind="equity",
                asset_class=asset_class_of("equity", None, name),
                units=units,
                price=dec(e.price) or Decimal(0),
                value=dec(e.value) or Decimal(0),
                symbol=e.symbol,
                price_date=period_end,
            )
            acc.holdings.append(h)
            acc.value += h.value
        for m in a.mutual_funds or []:
            units = dec(m.balance) or Decimal(0)
            if units == 0:
                continue
            name = _title(m.name) or m.isin
            h = CasHolding(
                isin=m.isin,
                name=name,
                kind="mf",
                asset_class=asset_class_of("mf", m.type, name),
                units=units,
                price=dec(m.nav) or Decimal(0),
                value=dec(m.value) or Decimal(0),
                cost=dec(m.total_cost),
                folio=folio_tag(m.folio),
                amfi_code=m.amfi,
                price_date=period_end,
            )
            acc.holdings.append(h)
            acc.value += h.value
        for b in a.bonds or []:
            units = dec(b.num_bonds) or Decimal(0)
            if units == 0:
                continue
            value = dec(b.value) or Decimal(0)
            price = dec(b.market_price) or (value / units if units else Decimal(0))
            h = CasHolding(
                isin=b.isin,
                name=_title(b.name) or b.isin,
                kind="bond",
                asset_class="debt",
                units=units,
                price=price,
                value=value,
                price_date=period_end,
            )
            acc.holdings.append(h)
            acc.value += h.value
        if acc.holdings:
            # Several "Mutual Fund Folios" blocks belong to the same investor account.
            same = next((x for x in out if x.key == acc.key), None)
            if same:
                same.holdings += acc.holdings
                same.value += acc.value
            else:
                out.append(acc)
    nps = _attr(raw, "nps")
    if nps and nps.schemes:
        acc = CasAccount(
            key="nps:" + _hash(nps.pran or who),
            kind="nps",
            name="NPS",
            institution=nps.nps_sp,
            mask=_digits_tail(nps.pran),
            value=Decimal(0),
        )
        for s in nps.schemes:
            units = dec(s.units) or Decimal(0)
            if units == 0:
                continue
            asset = {"E": "equity", "C": "debt", "G": "debt", "A": "other"}.get(
                (s.asset_class or "").upper(), "other"
            )
            h = CasHolding(
                isin=_synthetic_isin("NPS", s.scheme),
                name=re.sub(r"\s+", " ", s.scheme).strip(),
                kind="nps",
                asset_class=asset,
                units=units,
                price=dec(s.nav) or Decimal(0),
                value=dec(s.value) or Decimal(0),
                price_date=period_end,
            )
            acc.holdings.append(h)
            acc.value += h.value
        if acc.holdings:
            out.append(acc)
    file_type = str(_attr(raw.file_type, "value", raw.file_type))
    return CasResult(file_type, None, period_start, period_end, out, list(raw.parse_warnings or []))


def from_casparser(raw: Any) -> CasResult:
    if hasattr(raw, "folios"):
        return _from_rta(raw)
    if hasattr(raw, "accounts"):
        return _from_depository(raw)
    raise CasError("casparser returned something we don't understand.")


def read(data: bytes, password: str) -> CasResult:
    """Parse a CAS PDF from bytes. The password is never logged or put in an error."""
    import casparser
    from casparser.exceptions import IncorrectPasswordError, ParserException

    failure: str | None = None
    raw: Any = None
    try:
        raw = casparser.read_cas_pdf(io.BytesIO(data), password)
    except IncorrectPasswordError:
        failure = "The saved password doesn't open this CAS."
    except ParserException as exc:
        failure = f"casparser couldn't read this CAS ({type(exc).__name__})."
        log.warning("casparser failed: %s", type(exc).__name__)
    except Exception as exc:  # noqa: BLE001 - a parser bug shouldn't take the app down
        failure = f"casparser failed on this file ({type(exc).__name__})."
        log.warning("casparser crashed: %s", type(exc).__name__)
    if failure:
        # Raised outside the except blocks so nothing from the PDF is chained onto it.
        raise CasError(failure)
    return from_casparser(raw)
