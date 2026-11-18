"""XIRR: the yearly return that makes a set of dated cash flows add up to zero.

Same definition as the spreadsheet function: money you put in is negative, money you get back
(and what you hold today) is positive, and time is counted in days over 365.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date

Flow = tuple[date, float]


def _npv(rate: float, flows: Sequence[Flow], d0: date) -> float:
    return float(sum(cf / (1.0 + rate) ** ((d - d0).days / 365.0) for d, cf in flows))


def _dnpv(rate: float, flows: Sequence[Flow], d0: date) -> float:
    total = 0.0
    for d, cf in flows:
        t = (d - d0).days / 365.0
        total -= t * cf / (1.0 + rate) ** (t + 1.0)
    return total


def xirr(flows: Sequence[Flow], guess: float = 0.1) -> float | None:
    """The rate as a fraction (0.134 = 13.4% a year), or None when there isn't one."""
    flows = [(d, float(cf)) for d, cf in flows if cf]
    if not any(cf < 0 for _, cf in flows) or not any(cf > 0 for _, cf in flows):
        return None
    flows.sort(key=lambda f: f[0])
    d0 = flows[0][0]
    if all(d == d0 for d, _ in flows):
        return None

    # Newton's method first: fast, and lands where spreadsheets do.
    rate = guess
    for _ in range(100):
        try:
            f = _npv(rate, flows, d0)
            df = _dnpv(rate, flows, d0)
        except (OverflowError, ZeroDivisionError):
            break
        if df == 0 or math.isnan(f) or math.isnan(df):
            break
        step = f / df
        nxt = rate - step
        if nxt <= -0.999999:
            nxt = (rate - 0.999999) / 2
        if abs(nxt - rate) < 1e-10:
            return nxt
        rate = nxt

    # Bisection as a fallback: slower, but it can't wander off.
    lo, hi = -0.999999, 100.0
    try:
        f_lo, f_hi = _npv(lo, flows, d0), _npv(hi, flows, d0)
    except (OverflowError, ZeroDivisionError):
        return None
    if f_lo * f_hi > 0:
        return None
    for _ in range(300):
        mid = (lo + hi) / 2
        f_mid = _npv(mid, flows, d0)
        if abs(f_mid) < 1e-9 or hi - lo < 1e-12:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2
