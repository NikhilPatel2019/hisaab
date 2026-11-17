"""Vault, PDF handling, password recipes and classification, without the web app."""

from __future__ import annotations

import os
import stat
from datetime import date
from pathlib import Path

import pytest

from hisaab.docs import passwords
from hisaab.docs.classify import classify, find_masks, find_period, is_known_sender
from hisaab.docs.pdf import NotAPdf, decrypted_copy, first_that_opens, inspect, try_password
from hisaab.docs.vault import Vault, VaultError, sha256_of
from hisaab.models import Account, DocRule

from .pdfs import bank_statement, card_statement, cas, lock, make_pdf

FACTS = {"name": "Asha Verma", "dob": "1990-03-07", "pan": "ABCDE1234F", "mobile": "9876543210"}


# --- vault ------------------------------------------------------------------------------------


def test_vault_keeps_exact_bytes_once_and_read_only(tmp_path: Path) -> None:
    vault = Vault(tmp_path)
    data = lock(make_pdf(["hello"]), "pw-1")
    sha = vault.put(data)
    assert sha == sha256_of(data)
    assert vault.put(data) == sha  # second put is a no-op
    path = vault.path_for(sha)
    assert path.read_bytes() == data and vault.get(sha) == data
    assert not stat.S_IMODE(path.stat().st_mode) & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == [f"{sha}.bin"]


def test_vault_detects_tampering_and_bad_ids(tmp_path: Path) -> None:
    vault = Vault(tmp_path)
    sha = vault.put(b"%PDF-1.4 original")
    path = vault.path_for(sha)
    os.chmod(path, 0o644)
    path.write_bytes(b"%PDF-1.4 changed")
    with pytest.raises(VaultError, match="fingerprint"):
        vault.get(sha)
    with pytest.raises(VaultError):
        vault.path_for("../../etc/passwd")
    with pytest.raises(VaultError, match="missing"):
        vault.get("0" * 64)


# --- pdf --------------------------------------------------------------------------------------


def test_pdf_inspect_try_and_decrypt() -> None:
    plain = make_pdf(["Statement of Account", "Opening Balance"])
    info = inspect(plain)
    assert not info.encrypted and info.pages == 1 and "Statement of Account" in (info.text or "")

    locked = lock(plain, "s3cret-pw")
    assert inspect(locked).encrypted and inspect(locked).text is None
    assert try_password(locked, "nope") is None
    opened = try_password(locked, "s3cret-pw")
    assert opened and "Opening Balance" in (opened.text or "")

    found = first_that_opens(locked, [("a", "x"), ("b", "y"), ("c", "s3cret-pw"), ("d", "z")])
    assert found and found[0] == "c"
    assert first_that_opens(locked, [("a", "x")]) is None

    copy = decrypted_copy(locked, "s3cret-pw")
    assert not inspect(copy).encrypted and "Statement" in (inspect(copy).text or "")
    with pytest.raises(PermissionError) as err:
        decrypted_copy(locked, "wrong-guess")
    assert "wrong-guess" not in str(err.value)

    owner_only = lock(plain, "")
    info = inspect(owner_only)
    assert info.encrypted and "Statement" in (info.text or "")

    with pytest.raises(NotAPdf):
        inspect(b"PK\x03\x04 a zip file")


# --- password recipes -------------------------------------------------------------------------


def test_recipes_cover_common_indian_formats_and_ids_never_hold_passwords() -> None:
    pairs = list(passwords.recipes(FACTS, ["9876"]))
    values = {pw for _, pw in pairs}
    for expected in ("ABCDE1234F", "ASHA0703", "asha0703", "07031990", "ASHA9876", "43210070390"):
        assert expected in values, expected
    for rid, pw in pairs:
        assert pw not in rid
        for fact in FACTS.values():
            assert fact not in rid


def test_facts_are_validated() -> None:
    assert passwords.normalise_fact("pan", " abcde1234f ") == "ABCDE1234F"
    assert passwords.normalise_fact("mobile", "+91 98765 43210") == "9876543210"
    for key, bad in (("pan", "12345"), ("dob", "07/03/1990"), ("mobile", "123"), ("name", "1234")):
        with pytest.raises(passwords.FactError):
            passwords.normalise_fact(key, bad)


def test_describe_labels() -> None:
    assert passwords.describe("recipe:name4_upper+dob_ddmm") == "name + date of birth"
    assert passwords.describe("recipe:pan") == "PAN"
    assert passwords.describe("secret:pdf.pw.hdfc-card") == "saved password “hdfc-card”"
    assert passwords.describe("empty") == "no password needed"


# --- classification ---------------------------------------------------------------------------


def _text(lines: list[str]) -> str:
    info = inspect(make_pdf(lines))
    return info.text or ""


def _accounts() -> list[Account]:
    return [
        Account(
            id=1, name="Salary", kind="bank", institution="HDFC Bank", mask="4321", archived=False
        ),
        Account(
            id=2,
            name="Card",
            kind="credit_card",
            institution="SBI Card",
            mask="9876",
            archived=False,
        ),
        Account(id=3, name="Old", kind="bank", institution="Axis Bank", mask="1111", archived=True),
    ]


def test_builtin_rules_file_common_statements() -> None:
    c = classify(
        text=_text(bank_statement(4, 2026)),
        sender="alerts@hdfcbank.net",
        filename="Acct.pdf",
        accounts=_accounts(),
    )
    assert (c.kind, c.institution, c.account_id) == ("bank_statement", "HDFC Bank", 1)
    assert (c.period_start, c.period_end) == (date(2026, 4, 1), date(2026, 4, 30))
    assert c.classified_by == "builtin:bank_statement"

    c = classify(
        text=_text(card_statement(3, 2026)),
        sender="Statements@sbicard.com",
        filename="x.pdf",
        accounts=_accounts(),
    )
    assert (c.kind, c.institution, c.account_id) == ("card_statement", "SBI Card", 2)
    assert (c.period_start, c.period_end) == (date(2026, 3, 5), date(2026, 4, 4))

    c = classify(text=_text(cas(2, 2026)), sender="ecas@cdslstatement.com", filename="CAS.pdf")
    assert (c.kind, c.institution) == ("cas", "CDSL")

    c = classify(
        text=None, sender="no-reply-contract-notes@reportsmailer.zerodha.net", filename="CN.pdf"
    )
    assert (c.kind, c.institution) == ("broker", "Zerodha")

    c = classify(
        text=_text(["Annual Information Statement (AIS)", "Income Tax Department"]),
        sender=None,
        filename="ais.pdf",
    )
    assert (c.kind, c.institution) == ("tax", "Income Tax Department")

    c = classify(
        text=_text(["Payslip for March 2026", "Earnings 1 Deductions 2 Net Pay 3"]),
        sender=None,
        filename="March.pdf",
    )
    assert c.kind == "payslip"

    c = classify(
        text=_text(["Your movie ticket", "Screen 3 Row F"]),
        sender="tickets@cinema.example",
        filename="ticket.pdf",
    )
    assert c.kind == "unknown" and c.classified_by is None

    # A bare "statement" file name from an unknown sender isn't enough.
    c = classify(text=None, sender="someone@example.com", filename="statement.pdf")
    assert c.kind == "unknown"


def test_your_rules_win_over_builtin_ones() -> None:
    rule = DocRule(
        id=7,
        name="Co-op bank",
        match_sender="@coop.example",
        kind="bank_statement",
        institution="Co-op",
        account_id=None,
    )
    c = classify(
        text=_text(card_statement(1)),
        sender="estatement@coop.example",
        filename="a.pdf",
        rules=[rule],
    )
    assert (c.kind, c.institution, c.classified_by) == ("bank_statement", "Co-op", "rule:7")
    glob_rule = DocRule(id=8, name="Payslips", match_filename="payroll_*.pdf", kind="payslip")
    c = classify(text=None, sender=None, filename="Payroll_2026_03.pdf", rules=[glob_rule])
    assert c.classified_by == "rule:8"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Statement Period: 01-Apr-2026 to 30-Apr-2026", (date(2026, 4, 1), date(2026, 4, 30))),
        ("for the period 1st April 2026 - 30th April 2026", (date(2026, 4, 1), date(2026, 4, 30))),
        ("From 2026-04-01 to 2026-04-30", (date(2026, 4, 1), date(2026, 4, 30))),
        ("from 01.04.26 till 30.04.26", (date(2026, 4, 1), date(2026, 4, 30))),
        ("30/04/2026 to 01/04/2026", (None, None)),
        ("nothing here", (None, None)),
    ],
)
def test_find_period(text: str, expected: tuple[date | None, date | None]) -> None:
    assert find_period(text) == expected


def test_masks_and_known_senders() -> None:
    assert find_masks("Card XXXX XXXX XXXX 1234 and a/c xx5678, ending with 9012") == [
        "1234",
        "5678",
        "9012",
    ]
    assert is_known_sender("Statements <estatement@mail.hdfcbank.net>")
    assert not is_known_sender("offers@shop.example")
    assert is_known_sender("offers@shop.example", ["shop.example"])
