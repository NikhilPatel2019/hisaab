"""Exact arithmetic for units, prices and money.

Units and prices are stored as whole millionths (`E6`), money as whole paise, so nothing is
lost to floating point on any database.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from hisaab.models import E6

_E6 = Decimal(E6)
_PAISE_PER_E12 = Decimal(E6 * E6) / 100


def dec(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def to_e6(value: object) -> int | None:
    d = dec(value)
    return None if d is None else int((d * _E6).to_integral_value(ROUND_HALF_UP))


def to_paise(value: object) -> int | None:
    d = dec(value)
    return None if d is None else int((d * 100).to_integral_value(ROUND_HALF_UP))


def from_e6(n: int | None) -> Decimal | None:
    return None if n is None else Decimal(n) / _E6


def value_paise(units_e6: int, price_e6: int) -> int:
    """units × price, in paise, rounded half up: the way statements round."""
    return int(
        (Decimal(units_e6) * Decimal(price_e6) / _PAISE_PER_E12).to_integral_value(ROUND_HALF_UP)
    )


def e6_str(n: int | None, places: int = 4) -> str | None:
    """A unit count or price as text, without trailing zeros beyond `places`."""
    if n is None:
        return None
    d = (Decimal(n) / _E6).quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP)
    text = format(d, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text
