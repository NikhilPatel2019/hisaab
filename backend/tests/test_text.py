from __future__ import annotations

from datetime import date

import pytest

from hisaab.ledger.text import detect_date_format, guess_payee, parse_amount, parse_date


@pytest.mark.parametrize(
    ("raw", "paise"),
    [
        ("1,23,456.78", 12345678),
        ("₹ 500", 50000),
        ("Rs. 1,000.50", 100050),
        ("INR 20", 2000),
        ("-250.00", -25000),
        ("(1,200.00)", -120000),
        ("1,000.00 Cr", 100000),
        ("1,000.00 Dr", -100000),
        ("450.00DR.", -45000),
        ("+75", 7500),
        ("0.005", 0),
    ],
)
def test_parse_amount(raw: str, paise: int) -> None:
    assert parse_amount(raw) == paise


@pytest.mark.parametrize("raw", ["", " ", "-", "--", "NA", None])
def test_blank_amounts_are_none(raw: str | None) -> None:
    assert parse_amount(raw) is None


def test_garbage_amount_raises() -> None:
    with pytest.raises(ValueError, match="isn't an amount"):
        parse_amount("twelve")


def test_day_first_wins_when_ambiguous() -> None:
    assert detect_date_format(["03/04/2026", "05/04/2026"]) == "%d/%m/%Y"


def test_month_first_only_when_forced() -> None:
    assert detect_date_format(["04/13/2026", "04/20/2026"]) == "%m/%d/%Y"


@pytest.mark.parametrize(
    ("samples", "fmt", "first"),
    [
        (["05-10-26", "31-10-26"], "%d-%m-%y", date(2026, 10, 5)),
        (["12 Oct 2026", "1 Nov 2026"], "%d %b %Y", date(2026, 10, 12)),
        (["12-Oct-2026"], "%d-%b-%Y", date(2026, 10, 12)),
        (["2026-10-12"], "%Y-%m-%d", date(2026, 10, 12)),
        (["12/10/2026 10:41:07"], "%d/%m/%Y", date(2026, 10, 12)),
        (["12/10/2026 09:41 PM"], "%d/%m/%Y", date(2026, 10, 12)),
    ],
)
def test_detects_common_bank_formats(samples: list[str], fmt: str, first: date) -> None:
    assert detect_date_format(samples) == fmt
    assert parse_date(samples[0], fmt) == first


@pytest.mark.parametrize(
    ("desc", "payee"),
    [
        ("UPI/R K SHAH/KKBK/600000000404/oct rent", "R K Shah"),
        ("UPI-GREENCART GROCERS-greencart@ybl-YESB0000001-600000000123-UPI", "Greencart Grocers"),
        ("UPI/P2M/600000000505/QUICKBITE", "Quickbite"),
        ("SHOPKART ONLINE", "SHOPKART ONLINE"),
    ],
)
def test_guess_payee(desc: str, payee: str) -> None:
    assert guess_payee(desc) == payee
