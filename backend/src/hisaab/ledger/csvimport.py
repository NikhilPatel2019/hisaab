"""Bring a bank or card CSV into one account, safely and repeatably.

- Finds the header even when the bank puts account details above it.
- Guesses which column is which, and the date format (day-first by default).
- Handles a signed amount column, separate debit/credit columns, or amount + Dr/Cr.
- Every row gets a fingerprint, so importing the same file again adds nothing.
- If the file has a running balance, we use it to check the result adds up.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hisaab.ledger.text import (
    DATE_FORMATS,
    detect_date_format,
    guess_payee,
    normalise_text,
    parse_amount,
    parse_date,
    strip_time,
)
from hisaab.models import Account, Category, ImportBatch, Transaction

MAX_CHARS = 5_000_000
AmountMode = Literal["single", "split", "type"]


class Mapping(BaseModel):
    """Which column holds what. Column numbers start at 0."""

    header_row: int | None = Field(default=None, ge=0)
    date_col: int = Field(ge=0)
    date_format: str
    description_cols: list[int] = Field(min_length=1)
    amount_mode: AmountMode
    amount_col: int | None = None
    debit_col: int | None = None
    credit_col: int | None = None
    type_col: int | None = None
    invert: bool = False
    balance_col: int | None = None
    reference_col: int | None = None
    category_col: int | None = None


class ImportProblem(Exception):
    pass


@dataclass
class ParsedRow:
    line: int
    posted_on: date
    amount_paise: int
    description: str
    reference: str | None
    balance_paise: int | None
    category: str | None


@dataclass
class ParseResult:
    rows: list[ParsedRow] = field(default_factory=list)
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    descending: bool = False


# --- reading the file ----------------------------------------------------------------------


def read_table(content: str) -> list[list[str]]:
    if len(content) > MAX_CHARS:
        raise ImportProblem("That file is too big for one import (5 MB). Split it by year.")
    text = content.lstrip("﻿")
    sample = text[:8192]
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), dialect)]
    return [r for r in rows if any(r)]


_DATE_WORDS = ("date",)
_DESC_WORDS = ("narration", "description", "particulars", "details", "remarks", "merchant")
_DEBIT_WORDS = ("debit", "withdrawal", "dr")
_CREDIT_WORDS = ("credit", "deposit", "cr")
_AMOUNT_WORDS = ("amount", "amt", "inr")
_TYPE_WORDS = ("type", "dr/cr", "cr/dr", "dr / cr")
_BALANCE_WORDS = ("balance", "bal")
_REF_WORDS = ("ref", "chq", "cheque", "utr")


def _has(cell: str, words: tuple[str, ...]) -> bool:
    c = cell.lower()
    tokens = c.replace(".", " ").replace("/", " / ").split()
    return any(w in tokens or (len(w) > 3 and w in c) for w in words)


def find_header(rows: list[list[str]]) -> int | None:
    for i, row in enumerate(rows[:40]):
        if len([c for c in row if c]) < 3:
            continue
        has_date = any(_has(c, _DATE_WORDS) for c in row)
        others = sum(
            any(_has(c, w) for c in row)
            for w in (_DESC_WORDS, _DEBIT_WORDS + _CREDIT_WORDS + _AMOUNT_WORDS, _BALANCE_WORDS)
        )
        if has_date and others >= 1:
            return i
    return None


def _first(header: list[str], words: tuple[str, ...], exclude: tuple[str, ...] = ()) -> int | None:
    for i, cell in enumerate(header):
        if _has(cell, words) and not (exclude and _has(cell, exclude)):
            return i
    return None


def suggest_mapping(rows: list[list[str]]) -> Mapping:
    header_row = find_header(rows)
    if header_row is None:
        raise ImportProblem(
            "Couldn't find a header row with a date column. Is this a transactions export?"
        )
    header = rows[header_row]
    body = rows[header_row + 1 :]

    date_candidates = [i for i, c in enumerate(header) if _has(c, _DATE_WORDS)]
    preferred = [i for i in date_candidates if not _has(header[i], ("value",))]
    date_col = (preferred or date_candidates)[0]

    desc = _first(header, _DESC_WORDS)
    debit = _first(header, _DEBIT_WORDS, exclude=("card",))
    credit = _first(header, _CREDIT_WORDS, exclude=("card",))
    amount = _first(header, _AMOUNT_WORDS, exclude=_DEBIT_WORDS + _CREDIT_WORDS + _BALANCE_WORDS)
    kind = _first(header, _TYPE_WORDS)
    if kind is not None and kind in (debit, credit):
        debit = credit = None  # a "Dr/Cr" column is a type column, not two amount columns
    balance = _first(header, _BALANCE_WORDS)
    ref = _first(header, _REF_WORDS)
    category = _first(header, ("category",))

    if debit is not None and credit is not None and debit != credit:
        mode: AmountMode = "split"
    elif amount is not None and kind is not None:
        mode = "type"
    elif amount is not None:
        mode = "single"
    else:
        raise ImportProblem("Couldn't find an amount column (or debit and credit columns).")

    if desc is None:
        used = {date_col, debit, credit, amount, kind, balance, ref, category}
        desc = next((i for i in range(len(header)) if i not in used), 0)

    samples = [r[date_col] for r in body[:200] if len(r) > date_col and r[date_col]]
    fmt = detect_date_format(samples[:60]) or _majority_format(samples)
    if fmt is None:
        raise ImportProblem(f"Couldn't read the dates in '{header[date_col]}'.")

    return Mapping(
        header_row=header_row,
        date_col=date_col,
        date_format=fmt,
        description_cols=[desc],
        amount_mode=mode,
        amount_col=amount if mode != "split" else None,
        debit_col=debit if mode == "split" else None,
        credit_col=credit if mode == "split" else None,
        type_col=kind if mode == "type" else None,
        balance_col=balance,
        reference_col=ref,
        category_col=category,
    )


def _majority_format(samples: list[str]) -> str | None:
    """Footers like 'Total' break all-or-nothing detection; take the format most rows fit."""
    best, best_hits = None, 0
    for fmt in DATE_FORMATS:
        hits = 0
        for s in samples:
            try:
                parse_date(strip_time(s), fmt)
                hits += 1
            except ValueError:
                pass
        if hits > best_hits:
            best, best_hits = fmt, hits
    return best if best_hits >= max(1, len(samples) // 2) else None


# --- turning rows into transactions --------------------------------------------------------


def _cell(row: list[str], col: int | None) -> str:
    return row[col] if col is not None and col < len(row) else ""


def _amount(row: list[str], m: Mapping) -> int:
    if m.amount_mode == "single":
        raw = _cell(row, m.amount_col)
        value = parse_amount(raw)
        if value is None:
            raise ValueError("no amount")
        if raw.strip().upper().rstrip(".").endswith(("CR", "DR")):
            return value  # an explicit Cr/Dr already says which way the money went
        return -value if m.invert else value
    if m.amount_mode == "split":
        debit = parse_amount(_cell(row, m.debit_col))
        credit = parse_amount(_cell(row, m.credit_col))
        if debit is None and credit is None:
            raise ValueError("no debit or credit")
        return abs(credit or 0) - abs(debit or 0)
    value = parse_amount(_cell(row, m.amount_col))
    if value is None:
        raise ValueError("no amount")
    kind = _cell(row, m.type_col).strip().upper()
    if kind.startswith(("DR", "D", "WITHDRAW", "DEBIT")):
        return -abs(value)
    if kind.startswith(("CR", "C", "DEPOSIT", "CREDIT")):
        return abs(value)
    raise ValueError(f"'{kind or 'blank'}' isn't Dr or Cr")


def parse_rows(rows: list[list[str]], m: Mapping) -> ParseResult:
    out = ParseResult()
    start = (m.header_row + 1) if m.header_row is not None else 0
    for offset, row in enumerate(rows[start:]):
        line = start + offset + 1
        raw_date = _cell(row, m.date_col)
        try:
            posted_on = parse_date(raw_date, m.date_format)
        except ValueError:
            out.skipped += 1  # opening-balance lines, totals, footers
            continue
        try:
            amount = _amount(row, m)
            balance = parse_amount(_cell(row, m.balance_col)) if m.balance_col is not None else None
        except ValueError as exc:
            out.skipped += 1
            if len(out.errors) < 20:
                out.errors.append(f"Line {line}: {exc}")
            continue
        description = (
            " ".join(_cell(row, c) for c in m.description_cols if _cell(row, c)).strip()
            or "(no description)"
        )
        out.rows.append(
            ParsedRow(
                line=line,
                posted_on=posted_on,
                amount_paise=amount,
                description=description[:500],
                reference=(_cell(row, m.reference_col) or None)
                if m.reference_col is not None
                else None,
                balance_paise=balance,
                category=_cell(row, m.category_col) or None,
            )
        )
    if len(out.rows) >= 2 and out.rows[0].posted_on > out.rows[-1].posted_on:
        out.descending = True
    return out


def fingerprints(rows: list[ParsedRow]) -> list[str]:
    """Stable per row. Two identical rows on the same day (two ₹20 teas) get #0 and #1."""
    seen: Counter[str] = Counter()
    out = []
    for r in rows:
        key = "|".join(
            [
                r.posted_on.isoformat(),
                str(r.amount_paise),
                normalise_text(r.description),
                normalise_text(r.reference or ""),
            ]
        )
        n = seen[key]
        seen[key] += 1
        out.append(hashlib.sha256(f"{key}#{n}".encode()).hexdigest())
    return out


def existing_hashes(session: Session, account_id: int, hashes: list[str]) -> set[str]:
    found: set[str] = set()
    for i in range(0, len(hashes), 500):
        chunk = hashes[i : i + 500]
        found.update(
            h
            for h in session.scalars(
                select(Transaction.dedupe_hash).where(
                    Transaction.account_id == account_id, Transaction.dedupe_hash.in_(chunk)
                )
            )
            if h
        )
    return found


# --- preview and import ---------------------------------------------------------------------


def check_mapping(table: list[list[str]], m: Mapping) -> None:
    """A mapping sent by a client must fit this file."""
    if m.header_row is not None and m.header_row >= len(table):
        raise ImportProblem("The header row is past the end of the file.")
    width = max(len(r) for r in table)
    cols = [
        m.date_col,
        *m.description_cols,
        m.amount_col,
        m.debit_col,
        m.credit_col,
        m.type_col,
        m.balance_col,
        m.reference_col,
        m.category_col,
    ]
    if any(c is not None and c >= width for c in cols):
        raise ImportProblem("A chosen column isn't in this file.")
    needed = {
        "single": [m.amount_col],
        "split": [m.debit_col, m.credit_col],
        "type": [m.amount_col, m.type_col],
    }[m.amount_mode]
    if any(c is None for c in needed):
        raise ImportProblem("Pick the amount columns for this layout.")


class PreviewRow(BaseModel):
    line: int
    posted_on: date
    description: str
    amount_paise: int
    balance_paise: int | None
    duplicate: bool


class Preview(BaseModel):
    columns: list[str]
    mapping: Mapping
    rows_found: int
    rows_new: int
    rows_duplicate: int
    rows_skipped: int
    first_date: date | None
    last_date: date | None
    errors: list[str]
    sample: list[PreviewRow]


def preview(session: Session, account: Account, content: str, mapping: Mapping | None) -> Preview:
    table = read_table(content)
    if not table:
        raise ImportProblem("That file is empty.")
    m = mapping or suggest_mapping(table)
    check_mapping(table, m)
    if mapping is None and account.kind == "credit_card" and m.amount_mode == "single":
        # Card exports usually list spends as positive numbers. Flip them so a spend is
        # money out, unless most rows are already negative.
        trial = parse_rows(table, m).rows
        if trial and sum(r.amount_paise > 0 for r in trial) > len(trial) / 2:
            m = m.model_copy(update={"invert": True})
    columns = (
        table[m.header_row]
        if m.header_row is not None
        else [f"Column {i + 1}" for i in range(max(len(r) for r in table))]
    )
    parsed = parse_rows(table, m)
    hashes = fingerprints(parsed.rows)
    dupes = existing_hashes(session, account.id, hashes)
    dates = [r.posted_on for r in parsed.rows]
    return Preview(
        columns=columns,
        mapping=m,
        rows_found=len(parsed.rows),
        rows_new=sum(1 for h in hashes if h not in dupes),
        rows_duplicate=sum(1 for h in hashes if h in dupes),
        rows_skipped=parsed.skipped,
        first_date=min(dates) if dates else None,
        last_date=max(dates) if dates else None,
        errors=parsed.errors,
        sample=[
            PreviewRow(
                line=r.line,
                posted_on=r.posted_on,
                description=r.description,
                amount_paise=r.amount_paise,
                balance_paise=r.balance_paise,
                duplicate=h in dupes,
            )
            for r, h in list(zip(parsed.rows, hashes, strict=True))[:8]
        ],
    )


class BalanceCheck(BaseModel):
    on: date
    file_paise: int
    ledger_paise: int
    matches: bool


class ImportResult(BaseModel):
    import_id: int
    rows_added: int
    rows_duplicate: int
    rows_skipped: int
    uncategorised: int
    opening_balance_set_paise: int | None
    same_file_as_import: int | None
    balance_check: BalanceCheck | None
    errors: list[str]


def run_import(
    session: Session, account: Account, filename: str, content: str, m: Mapping
) -> ImportResult:
    table = read_table(content)
    if not table:
        raise ImportProblem("That file is empty.")
    check_mapping(table, m)
    parsed = parse_rows(table, m)
    if not parsed.rows:
        raise ImportProblem("No transactions found with this mapping. Check the date column.")
    hashes = fingerprints(parsed.rows)
    dupes = existing_hashes(session, account.id, hashes)

    file_sha = hashlib.sha256(content.encode()).hexdigest()
    same_file = session.scalar(
        select(ImportBatch.id).where(
            ImportBatch.account_id == account.id, ImportBatch.file_sha256 == file_sha
        )
    )
    had_transactions = bool(
        session.scalar(select(func.count()).where(Transaction.account_id == account.id))
    )

    categories = {c.name.lower(): c.id for c in session.scalars(select(Category))}
    batch = ImportBatch(
        account_id=account.id,
        filename=filename[:255],
        file_sha256=file_sha,
        mapping=json.dumps(m.model_dump()),
    )
    session.add(batch)
    session.flush()

    added = uncategorised = 0
    for row, h in zip(parsed.rows, hashes, strict=True):
        if h in dupes:
            continue
        dupes.add(h)
        category_id = categories.get(row.category.lower()) if row.category else None
        uncategorised += category_id is None
        session.add(
            Transaction(
                account_id=account.id,
                posted_on=row.posted_on,
                amount_paise=row.amount_paise,
                description=row.description,
                payee=guess_payee(row.description),
                reference=row.reference,
                category_id=category_id,
                source="csv",
                import_id=batch.id,
                dedupe_hash=h,
            )
        )
        added += 1

    chronological = list(reversed(parsed.rows)) if parsed.descending else parsed.rows
    opening_set = None
    with_balance = [r for r in chronological if r.balance_paise is not None]
    if (
        with_balance
        and not had_transactions
        and account.opening_balance_paise == 0
        and account.tracking == "transactions"
    ):
        first = with_balance[0]
        assert first.balance_paise is not None
        account.opening_balance_paise = first.balance_paise - first.amount_paise
        opening_set = account.opening_balance_paise

    batch.rows_added = added
    batch.rows_duplicate = len(parsed.rows) - added
    batch.rows_skipped = parsed.skipped
    session.flush()

    check = None
    if with_balance:
        last = with_balance[-1]
        assert last.balance_paise is not None
        ledger = account.opening_balance_paise + (
            session.scalar(
                select(func.coalesce(func.sum(Transaction.amount_paise), 0)).where(
                    Transaction.account_id == account.id, Transaction.posted_on <= last.posted_on
                )
            )
            or 0
        )
        check = BalanceCheck(
            on=last.posted_on,
            file_paise=last.balance_paise,
            ledger_paise=int(ledger),
            matches=int(ledger) == last.balance_paise,
        )
    session.commit()
    return ImportResult(
        import_id=batch.id,
        rows_added=added,
        rows_duplicate=len(parsed.rows) - added,
        rows_skipped=parsed.skipped,
        uncategorised=uncategorised,
        opening_balance_set_paise=opening_set,
        same_file_as_import=same_file,
        balance_check=check,
        errors=parsed.errors,
    )
