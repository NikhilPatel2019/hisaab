"""Market prices: mutual fund NAVs from AMFI, share and ETF closes from the NSE/BSE bhavcopy.

Every fetch retries with backoff, and every price records its own date, so a page can say
when a price is stale instead of quietly showing an old number. Nothing here needs a key or
an account: these are the public end-of-day files.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hisaab.invest.importer import upsert_price
from hisaab.invest.num import dec, to_e6
from hisaab.models import Account, Holding, Instrument

log = logging.getLogger("hisaab.invest")

AMFI_NAV_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"
MFAPI_URL = "https://api.mfapi.in/mf/{code}"
NSE_BHAV_URL = (
    "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
)
BSE_BHAV_URL = (
    "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{d:%Y%m%d}_F_0000.CSV"
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) hisaab/0.1 (self-hosted; end-of-day prices)",
    "Accept": "*/*",
}
# A price older than this many days is shown as stale.
STALE_AFTER_DAYS = {"mf": 4, "equity": 5, "bond": 40, "nps": 40, "other": 40}


class Http(Protocol):
    def get(self, url: str, headers: dict[str, str] | None = ..., timeout: float = ...) -> Any: ...


def http_session() -> Http:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    s = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    session: Http = s  # type: ignore[assignment]
    return session


class PriceError(Exception):
    pass


def _get(http: Http, url: str, *, referer: str | None = None, timeout: float = 60) -> Any:
    headers = dict(HEADERS)
    if referer:
        headers["Referer"] = referer
    try:
        r = http.get(url, headers=headers, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - network errors are reported, never fatal
        raise PriceError(f"couldn't reach {url.split('/')[2]} ({type(exc).__name__})") from None
    if r.status_code == 404:
        raise FileNotFoundError(url)
    if r.status_code >= 400:
        raise PriceError(f"{url.split('/')[2]} answered {r.status_code}")
    return r


# --- parsers ----------------------------------------------------------------------------------


def parse_navall(text: str) -> dict[str, tuple[date, Decimal, str]]:
    """AMFI's NAVAll.txt → {ISIN: (date, NAV, scheme code)}. Header-driven, so a new column
    (AMFI added Plan and Option in 2026) doesn't break it."""
    out: dict[str, tuple[date, Decimal, str]] = {}
    cols: dict[str, int] = {}
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 5:
            continue
        if parts[0].lower().startswith("scheme code"):
            cols = {p.lower(): i for i, p in enumerate(parts)}
            continue
        if not parts[0].isdigit() or not cols:
            continue
        nav_i = next((i for k, i in cols.items() if k.startswith("net asset value")), None)
        date_i = cols.get("date")
        isin_is = [i for k, i in cols.items() if "isin" in k]
        if nav_i is None or date_i is None or nav_i >= len(parts) or date_i >= len(parts):
            continue
        nav = dec(parts[nav_i])
        try:
            on = datetime.strptime(parts[date_i], "%d-%b-%Y").date()
        except ValueError:
            continue
        if nav is None or nav <= 0:
            continue
        for i in isin_is:
            isin = parts[i] if i < len(parts) else ""
            if len(isin) == 12 and isin.startswith("IN"):
                out[isin] = (on, nav, parts[0])
    return out


def parse_mfapi(body: dict[str, Any]) -> list[tuple[date, Decimal]]:
    """api.mfapi.in history → [(date, NAV)], oldest first."""
    out = []
    for row in body.get("data", []) or []:
        try:
            on = datetime.strptime(row["date"], "%d-%m-%Y").date()
        except (KeyError, ValueError, TypeError):
            continue
        nav = dec(row.get("nav"))
        if nav is not None and nav > 0:
            out.append((on, nav))
    out.sort()
    return out


_SERIES_PREFERRED = ("EQ", "BE", "BZ", "SM", "ST", "A", "B", "T", "X", "XT", "M", "MT")


def parse_bhavcopy(text: str) -> dict[str, tuple[date, Decimal]]:
    """A UDiFF CM bhavcopy (NSE or BSE, July 2024 onwards) → {ISIN: (trade date, close)}."""
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header:
        return {}
    idx = {h.strip().lower(): i for i, h in enumerate(header)}
    i_isin, i_close = idx.get("isin"), idx.get("clspric")
    i_date, i_series = idx.get("traddt"), idx.get("sctysrs")
    if i_isin is None or i_close is None or i_date is None:
        raise PriceError("the bhavcopy's columns weren't what we expected")
    best: dict[str, tuple[int, date, Decimal]] = {}
    for row in reader:
        if len(row) <= max(i_isin, i_close, i_date):
            continue
        isin = row[i_isin].strip()
        close = dec(row[i_close].strip())
        try:
            on = date.fromisoformat(row[i_date].strip()[:10])
        except ValueError:
            continue
        if len(isin) != 12 or close is None or close <= 0:
            continue
        series = (
            row[i_series].strip().upper() if i_series is not None and i_series < len(row) else ""
        )
        rank = _SERIES_PREFERRED.index(series) if series in _SERIES_PREFERRED else 99
        if isin not in best or rank < best[isin][0]:
            best[isin] = (rank, on, close)
    return {isin: (on, close) for isin, (_, on, close) in best.items()}


def _unzip_first(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        name = next((n for n in z.namelist() if n.lower().endswith(".csv")), None)
        if name is None:
            raise PriceError("the bhavcopy zip had no CSV in it")
        return z.read(name).decode("utf-8", "replace")


# --- fetchers ---------------------------------------------------------------------------------


def fetch_navall(http: Http) -> dict[str, tuple[date, Decimal, str]]:
    return parse_navall(_get(http, AMFI_NAV_URL).text)


def fetch_mfapi(http: Http, amfi_code: str) -> list[tuple[date, Decimal]]:
    if not amfi_code.isdigit():
        return []
    try:
        return parse_mfapi(_get(http, MFAPI_URL.format(code=amfi_code)).json())
    except FileNotFoundError:
        return []
    except ValueError as exc:
        raise PriceError("api.mfapi.in sent something that isn't JSON") from exc


def fetch_bhavcopy(http: Http, on: date) -> tuple[str, dict[str, tuple[date, Decimal]]]:
    """The bhavcopy for a trading day, from NSE or else BSE. FileNotFoundError if neither has it."""
    errors: list[str] = []
    try:
        data = _get(http, NSE_BHAV_URL.format(d=on), referer="https://www.nseindia.com/").content
        return "nse", parse_bhavcopy(_unzip_first(data))
    except FileNotFoundError:
        pass
    except (PriceError, zipfile.BadZipFile) as exc:
        errors.append(f"NSE: {exc}")
    try:
        text = _get(http, BSE_BHAV_URL.format(d=on), referer="https://www.bseindia.com/").text
        return "bse", parse_bhavcopy(text)
    except FileNotFoundError:
        if errors:
            raise PriceError("; ".join(errors)) from None
        raise
    except PriceError as exc:
        errors.append(f"BSE: {exc}")
        raise PriceError("; ".join(errors)) from None


# --- refresh ----------------------------------------------------------------------------------


@dataclass
class RefreshResult:
    updated: int = 0
    missing: list[str] = field(default_factory=list)  # instrument names with no price found
    errors: list[str] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)  # "amfi" -> "17 Nov 2026"

    def text(self) -> str:
        parts = [f"{self.updated} prices updated"]
        if self.missing:
            parts.append(f"{len(self.missing)} not found")
        if self.errors:
            parts.append("; ".join(self.errors))
        return " · ".join(parts)


def held_instruments(session: Session) -> list[Instrument]:
    """Instruments in each holdings account's latest statement."""
    latest = (
        select(Holding.account_id, func.max(Holding.as_of).label("as_of"))
        .join(Account, Account.id == Holding.account_id)
        .where(Account.archived.is_(False))
        .group_by(Holding.account_id)
        .subquery()
    )
    ids = select(Holding.instrument_id).join(
        latest, (Holding.account_id == latest.c.account_id) & (Holding.as_of == latest.c.as_of)
    )
    return list(
        session.scalars(
            select(Instrument)
            .where(Instrument.id.in_(ids), Instrument.isin.not_like("DEMO%"))
            .order_by(Instrument.id)
        )
    )


def refresh(
    session: Session, http: Http, today: date, instruments: Iterable[Instrument] | None = None
) -> RefreshResult:
    """Fetch the latest prices for everything you hold."""
    res = RefreshResult()
    held = list(instruments) if instruments is not None else held_instruments(session)
    funds = [i for i in held if i.kind == "mf"]
    shares = [i for i in held if i.kind == "equity"]

    if funds:
        try:
            navs = fetch_navall(http)
        except (PriceError, FileNotFoundError) as exc:
            res.errors.append(f"AMFI: {exc}")
            navs = {}
        newest = None
        for inst in funds:
            hit = navs.get(inst.isin)
            if hit is None:
                if navs:
                    res.missing.append(inst.name)
                continue
            on, nav, code = hit
            upsert_price(session, inst.id, on, to_e6(nav) or 0, "amfi")
            if not inst.amfi_code:
                inst.amfi_code = code
            res.updated += 1
            newest = max(newest or on, on)
        if newest:
            res.sources["amfi"] = newest.isoformat()

    if shares:
        found: dict[str, tuple[date, Decimal]] = {}
        source = None
        last_error = None
        for back in range(0, 8):  # today may be a holiday, or before the file is out
            day = today - timedelta(days=back)
            if day.weekday() >= 5:
                continue
            try:
                source, found = fetch_bhavcopy(http, day)
                break
            except FileNotFoundError:
                continue
            except PriceError as exc:
                last_error = str(exc)
                break
        if source is None:
            res.errors.append(f"Bhavcopy: {last_error or 'no file for the last week'}")
        else:
            newest = None
            for inst in shares:
                quote = found.get(inst.isin)
                if quote is None:
                    res.missing.append(inst.name)
                    continue
                on, close = quote
                upsert_price(session, inst.id, on, to_e6(close) or 0, source)
                res.updated += 1
                newest = max(newest or on, on)
            if newest:
                res.sources[source] = newest.isoformat()
    session.commit()
    return res
