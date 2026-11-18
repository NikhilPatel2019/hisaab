"""Turning a CAS in the postbox into holdings.

A CAS is a complete picture of each account on its statement date, so importing one replaces
that account's holdings for that date: importing it again changes nothing, and an older CAS
imported later only fills in history.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from hisaab.docs import passwords
from hisaab.docs.vault import Vault, VaultError
from hisaab.invest import cas
from hisaab.invest.num import to_e6, to_paise
from hisaab.models import (
    Account,
    BalanceSnapshot,
    CasImport,
    Document,
    Holding,
    Instrument,
    InvestmentTxn,
    Price,
)
from hisaab.secretstore import SecretBox

log = logging.getLogger("hisaab.invest")

# Market prices beat a statement's price for the same day; a statement never overwrites them.
MARKET_SOURCES = ("amfi", "mfapi", "nse", "bse")


@dataclass
class ImportSummary:
    imported: int = 0
    failed: int = 0
    with_warnings: int = 0

    def text(self) -> str:
        parts = [f"{self.imported} CAS read"]
        if self.with_warnings:
            parts.append(f"{self.with_warnings} with warnings")
        if self.failed:
            parts.append(f"{self.failed} failed")
        return " · ".join(parts)


def password_for(session: Session, box: SecretBox | None, doc: Document) -> str:
    if not doc.encrypted or doc.unlocked_with in ("empty", "none"):
        return ""
    if not doc.unlocked_with:
        raise cas.CasError("This CAS is still locked. Add its password in the Postbox first.")
    pw = passwords.resolve(session, box, doc.unlocked_with) if box else None
    if pw is None:
        raise cas.CasError("The password that opened this CAS was removed.")
    return pw


def upsert_instrument(session: Session, h: cas.CasHolding) -> Instrument:
    inst = session.scalar(select(Instrument).where(Instrument.isin == h.isin))
    if inst is None:
        inst = Instrument(
            isin=h.isin,
            kind=h.kind,
            name=h.name[:200],
            amfi_code=h.amfi_code,
            symbol=h.symbol,
            asset_class=h.asset_class,
        )
        session.add(inst)
        session.flush()
        return inst
    # Fill in what we didn't know; keep what's there (you may have renamed it).
    if not inst.amfi_code and h.amfi_code:
        inst.amfi_code = h.amfi_code
    if not inst.symbol and h.symbol:
        inst.symbol = h.symbol
    if inst.asset_class == "other" and h.asset_class != "other":
        inst.asset_class = h.asset_class
    if inst.name == inst.isin and h.name != h.isin:
        inst.name = h.name[:200]
    return inst


def upsert_price(
    session: Session, instrument_id: int, on: object, price_e6: int, source: str
) -> None:
    row = session.scalar(
        select(Price).where(Price.instrument_id == instrument_id, Price.price_date == on)
    )
    if row is None:
        session.add(
            Price(instrument_id=instrument_id, price_date=on, price_e6=price_e6, source=source)
        )
    elif source in MARKET_SOURCES or row.source not in MARKET_SOURCES:
        row.price_e6, row.source = price_e6, source


def account_for(session: Session, acc: cas.CasAccount) -> Account:
    found = session.scalar(select(Account).where(Account.external_key == acc.key))
    if found is not None:
        if found.archived:
            found.archived = False
        return found
    a = Account(
        name=acc.name,
        kind="retirement" if acc.kind == "nps" else "investment",
        tracking="holdings",
        institution=acc.institution,
        mask=acc.mask,
        external_key=acc.key,
    )
    session.add(a)
    session.flush()
    return a


def _txn_hash(t: cas.CasTxn, n: int) -> str:
    key = "|".join(
        str(x)
        for x in (
            t.isin,
            t.folio,
            t.on.isoformat(),
            t.type,
            t.units,
            t.amount,
            t.description.upper(),
            n,
        )
    )
    return hashlib.sha256(key.encode()).hexdigest()


def apply(session: Session, result: cas.CasResult, doc_id: int | None) -> tuple[int, int]:
    """Write one parsed CAS. Returns (holdings, new transactions)."""
    holdings = txns = 0
    as_of = result.period_end
    for acc in result.accounts:
        account = account_for(session, acc)
        session.execute(
            delete(Holding).where(Holding.account_id == account.id, Holding.as_of == as_of)
        )
        merged: dict[tuple[int, str], Holding] = {}
        total = 0
        for h in acc.holdings:
            inst = upsert_instrument(session, h)
            units, price = to_e6(h.units) or 0, to_e6(h.price) or 0
            value, cost = to_paise(h.value) or 0, to_paise(h.cost)
            total += value
            if price:
                upsert_price(session, inst.id, h.price_date or as_of, price, "cas")
            k = (inst.id, h.folio)
            if k in merged:  # the same fund twice in one folio: add them up
                m = merged[k]
                m.units_e6 += units
                m.value_paise += value
                m.cost_paise = None if m.cost_paise is None or cost is None else m.cost_paise + cost
                continue
            merged[k] = Holding(
                account_id=account.id,
                instrument_id=inst.id,
                folio=h.folio,
                as_of=as_of,
                units_e6=units,
                price_e6=price,
                value_paise=value,
                cost_paise=cost,
                document_id=doc_id,
            )
        session.add_all(merged.values())
        holdings += len(merged)

        snap = session.scalar(
            select(BalanceSnapshot).where(
                BalanceSnapshot.account_id == account.id, BalanceSnapshot.as_of == as_of
            )
        )
        if snap is None:
            session.add(
                BalanceSnapshot(
                    account_id=account.id, as_of=as_of, balance_paise=total, source="cas"
                )
            )
        elif snap.source != "manual":
            snap.balance_paise, snap.source = total, "cas"

        seen: Counter[tuple[object, ...]] = Counter()
        existing = set(
            session.scalars(
                select(InvestmentTxn.dedupe_hash).where(InvestmentTxn.account_id == account.id)
            )
        )
        inst_ids = {i.isin: i.id for i in session.scalars(select(Instrument))}
        for t in acc.txns:
            ident = (t.isin, t.folio, t.on, t.type, t.units, t.amount, t.description.upper())
            seen[ident] += 1
            digest = _txn_hash(t, seen[ident])
            if digest in existing:
                continue
            if t.isin not in inst_ids:
                _add_txn_instrument(session, t, inst_ids)
            session.add(
                InvestmentTxn(
                    account_id=account.id,
                    instrument_id=inst_ids[t.isin],
                    folio=t.folio,
                    txn_date=t.on,
                    type=t.type[:24],
                    description=t.description[:200] or t.type,
                    units_e6=to_e6(t.units),
                    price_e6=to_e6(t.price),
                    amount_paise=to_paise(t.amount),
                    balance_e6=to_e6(t.balance),
                    dedupe_hash=digest,
                    document_id=doc_id,
                )
            )
            existing.add(digest)
            txns += 1
    return holdings, txns


def _add_txn_instrument(session: Session, t: cas.CasTxn, ids: dict[str, int]) -> None:
    """A fully redeemed fund has transactions but no holding: create its instrument."""
    inst = Instrument(
        isin=t.isin,
        kind="mf",
        name=(t.name or t.isin)[:200],
        asset_class=t.asset_class,
        amfi_code=t.amfi_code,
    )
    session.add(inst)
    session.flush()
    ids[t.isin] = inst.id


def import_document(
    session: Session, vault: Vault, box: SecretBox | None, doc: Document
) -> CasImport:
    row = session.scalar(select(CasImport).where(CasImport.document_id == doc.id))
    if row is None:
        row = CasImport(document_id=doc.id, status="failed", warnings="[]")
        session.add(row)
    try:
        data = vault.get(doc.sha256)
        result = cas.read(data, password_for(session, box, doc))
    except (cas.CasError, VaultError) as exc:
        row.status, row.message, row.holdings, row.txns = "failed", str(exc)[:500], 0, 0
        session.commit()
        return row
    try:
        holdings, txns = apply(session, result, doc.id)
    except Exception:
        session.rollback()
        log.error("Applying CAS document %s failed", doc.id, exc_info=True)
        row = session.scalar(select(CasImport).where(CasImport.document_id == doc.id)) or CasImport(
            document_id=doc.id, warnings="[]"
        )
        row.status, row.message = (
            "failed",
            "Something went wrong saving this CAS. The server log has details.",
        )
        session.add(row)
        session.commit()
        return row
    row.file_type = result.file_type
    row.period_end = result.period_end
    row.status = "warnings" if result.warnings else "ok"
    row.message = None
    row.warnings = json.dumps([w[:300] for w in result.warnings[:50]])
    row.holdings, row.txns = holdings, txns
    # Fill in the document's period if filing didn't find one.
    if doc.period_end is None:
        doc.period_start, doc.period_end = result.period_start, result.period_end
    session.commit()
    log.info("Imported CAS document %s: %s holdings, %s transactions", doc.id, holdings, txns)
    return row


def pending(session: Session, retry_failed: bool = False) -> list[Document]:
    done = select(CasImport.document_id)
    if retry_failed:
        done = done.where(CasImport.status != "failed")
    q = (
        select(Document)
        .where(Document.kind == "cas", Document.status == "filed", Document.id.not_in(done))
        .order_by(Document.period_end.is_(None), Document.period_end, Document.id)
    )
    return list(session.scalars(q))


def import_pending(
    session: Session, vault: Vault, box: SecretBox | None, *, retry_failed: bool = False
) -> ImportSummary:
    """Read every filed CAS that hasn't been read yet, oldest first."""
    summary = ImportSummary()
    for doc in pending(session, retry_failed):
        row = import_document(session, vault, box, doc)
        if row.status == "failed":
            summary.failed += 1
        else:
            summary.imported += 1
            if row.status == "warnings":
                summary.with_warnings += 1
    return summary
