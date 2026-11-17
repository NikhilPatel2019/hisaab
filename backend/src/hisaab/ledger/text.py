"""Reading amounts, dates and payees the way Indian bank exports write them."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_BLANKS = {"", "-", "--", "—", "na", "n/a", "nil"}
_CURRENCY = re.compile(r"(₹|inr|rs\.?)", re.IGNORECASE)
_TIME_SUFFIX = re.compile(
    r"^(.+?)[ T]\d{1,2}:\d{2}(:\d{2})?(\.\d+)?( ?[ap]\.?m\.?)?$", re.IGNORECASE
)

# Day-first formats come before month-first ones: Indian banks write 03/04/2026 for 3 April.
DATE_FORMATS = (
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d/%m/%y",
    "%d-%m-%y",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d %b %Y",
    "%d-%b-%Y",
    "%d %b %y",
    "%d-%b-%y",
    "%d %B %Y",
    "%d-%B-%Y",
    "%b %d, %Y",
    "%m/%d/%Y",
)


def parse_amount(text: str | None) -> int | None:
    """'1,23,456.78' → 12345678 paise. Understands ₹/Rs/INR, Dr/Cr suffixes, (brackets) and
    a leading minus. Returns None for an empty cell; raises ValueError for anything else."""
    if text is None:
        return None
    s = text.strip()
    if s.lower() in _BLANKS:
        return None
    negative = False
    upper = s.upper().rstrip(".")
    if upper.endswith("DR"):
        negative, s = True, s[: upper.rfind("DR")]
    elif upper.endswith("CR"):
        s = s[: upper.rfind("CR")]
    s = s.strip()
    if s.startswith("(") and s.endswith(")"):
        negative, s = not negative, s[1:-1]
    s = _CURRENCY.sub("", s).replace(",", "").replace(" ", "").replace(" ", "")
    if s.startswith("-"):
        negative, s = not negative, s[1:]
    elif s.startswith("+"):
        s = s[1:]
    if not s:
        return None
    try:
        value = Decimal(s)
    except InvalidOperation as exc:
        raise ValueError(f"'{text.strip()}' isn't an amount") from exc
    paise = int((value * 100).quantize(Decimal(1)))
    return -paise if negative else paise


def strip_time(text: str) -> str:
    m = _TIME_SUFFIX.match(text.strip())
    return m.group(1) if m else text.strip()


def parse_date(text: str, fmt: str) -> date:
    return datetime.strptime(strip_time(text), fmt).date()


def detect_date_format(samples: list[str]) -> str | None:
    """The first format (day-first preferred) that reads every sample."""
    values = [strip_time(s) for s in samples if s and s.strip()]
    if not values:
        return None
    for fmt in DATE_FORMATS:
        try:
            for v in values:
                datetime.strptime(v, fmt)
        except ValueError:
            continue
        return fmt
    return None


_UPI = re.compile(r"^(UPI|IMPS|NEFT|RTGS|MMT)[/\- :]+", re.IGNORECASE)
_NOISE = re.compile(r"^(P2[AM]|DR|CR|\d+|[A-Z]{4}0[A-Z0-9]{6})$", re.IGNORECASE)


def guess_payee(description: str) -> str | None:
    """'UPI/R K SHAH/KKBK/6000/rent' → 'R K Shah'. A first guess; M6 rules refine it."""
    d = " ".join(description.split())
    if not d:
        return None
    if _UPI.match(d):
        parts = [p.strip() for p in re.split(r"[/\-]", _UPI.sub("", d)) if p.strip()]
        for part in parts:
            if not _NOISE.match(part) and len(part) > 1:
                return part.title()[:200]
        return None
    return d[:60].strip()


def normalise_text(text: str) -> str:
    return " ".join(text.lower().split())
