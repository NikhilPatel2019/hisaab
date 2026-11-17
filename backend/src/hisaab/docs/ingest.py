"""Bringing documents in.

Every file goes through the same steps, whichever way it arrived:

1. Have we seen this exact item before (same mail attachment, same Drive revision, same file
   on disk)? Then do nothing — re-running a sync never adds anything twice.
2. Is it the same bytes as a document we already keep? Then note the sighting and stop.
3. Store the original in the vault, exactly as received, even if it's locked.
4. If it's password-protected, try the passwords and recipes you've saved. The decrypted
   copy stays in memory; only the id of the recipe that worked is stored.
5. Work out what it is and file it, or leave it in the queue for you.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from hisaab.docs import passwords
from hisaab.docs.classify import classify, detect_institution, sender_domain
from hisaab.docs.pdf import NotAPdf, PdfInfo, first_that_opens, inspect
from hisaab.docs.vault import Vault, VaultError, sha256_of
from hisaab.models import Account, DocRule, Document, DocumentSighting
from hisaab.secretstore import SecretBox

log = logging.getLogger("hisaab.docs")

MAX_BYTES = 40 * 1024 * 1024
MANUAL = "you"

Outcome = Literal["new", "duplicate", "seen", "rejected"]


@dataclass
class Incoming:
    data: bytes
    filename: str
    source: str
    source_ref: str
    sender: str | None = None
    subject: str | None = None
    received_at: datetime | None = None


@dataclass
class Result:
    outcome: Outcome
    document_id: int | None = None
    status: str | None = None
    message: str | None = None


@dataclass
class Summary:
    new: int = 0
    duplicate: int = 0
    seen: int = 0
    rejected: int = 0
    needs_password: int = 0
    needs_review: int = 0
    errors: list[str] = field(default_factory=list)

    def add(self, r: Result) -> None:
        setattr(self, r.outcome, getattr(self, r.outcome) + 1)
        if r.outcome == "new" and r.status in ("needs_password", "needs_review"):
            setattr(self, r.status, getattr(self, r.status) + 1)

    def text(self) -> str:
        parts = [f"{self.new} new"]
        if self.duplicate:
            parts.append(f"{self.duplicate} duplicate{'s' if self.duplicate != 1 else ''} skipped")
        if self.needs_password:
            parts.append(
                f"{self.needs_password} need{'s' if self.needs_password == 1 else ''} a password"
            )
        if self.needs_review:
            parts.append(f"{self.needs_review} to sort")
        if self.rejected:
            parts.append(f"{self.rejected} not a PDF")
        return " · ".join(parts)


@dataclass
class Context:
    """What classification needs, loaded once per run."""

    rules: Sequence[DocRule]
    accounts: Sequence[Account]

    @classmethod
    def load(cls, session: Session) -> Context:
        return cls(
            rules=list(session.scalars(select(DocRule).order_by(DocRule.id))),
            accounts=list(session.scalars(select(Account))),
        )


class Seen:
    """Which items from a source have already been handled."""

    def __init__(self, session: Session, source: str) -> None:
        self.session = session
        self.source = source

    def has(self, ref: str) -> bool:
        q = select(
            exists().where(
                DocumentSighting.source == self.source, DocumentSighting.source_ref == ref[:400]
            )
        )
        return bool(self.session.scalar(q))

    def has_prefix(self, prefix: str) -> bool:
        q = select(
            exists().where(
                DocumentSighting.source == self.source,
                DocumentSighting.source_ref.startswith(prefix, autoescape=True),
            )
        )
        return bool(self.session.scalar(q))

    def mark(self, ref: str, outcome: str = "skipped") -> None:
        if not self.has(ref):
            self.session.add(
                DocumentSighting(
                    document_id=None, source=self.source, source_ref=ref[:400], outcome=outcome
                )
            )
            self.session.commit()


def hint_keys(
    sender: str | None, filename: str | None, institution: str | None = None
) -> list[str]:
    keys = []
    domain = sender_domain(sender)
    if domain:
        keys.append("sender:" + domain)
    inst = institution or detect_institution(sender, None, filename)
    if inst:
        keys.append("inst:" + inst)
    return keys


def status_for(doc: Document) -> str:
    if doc.status == "ignored":
        return "ignored"
    if doc.encrypted and not doc.unlocked_with:
        return "needs_password"
    if doc.kind == "unknown":
        return "needs_review"
    return "filed"


def try_unlock(
    session: Session, box: SecretBox | None, data: bytes, keys: list[str]
) -> tuple[str, PdfInfo] | None:
    if box is None:
        return None
    first = passwords.hints(session, keys)
    found = first_that_opens(data, passwords.candidates(session, box, first))
    if found:
        passwords.remember(session, keys, found[0])
    return found


def apply_classification(doc: Document, text: str | None, ctx: Context) -> None:
    if doc.classified_by == MANUAL:
        doc.status = status_for(doc)
        return
    c = classify(
        text=text,
        sender=doc.sender,
        filename=doc.filename,
        subject=doc.subject,
        rules=ctx.rules,
        accounts=ctx.accounts,
    )
    doc.kind = c.kind
    doc.institution = c.institution
    doc.account_id = c.account_id
    doc.period_start = c.period_start
    doc.period_end = c.period_end
    doc.classified_by = c.classified_by
    doc.status = status_for(doc)


def _sight(session: Session, item: Incoming, document_id: int | None, outcome: str) -> None:
    session.add(
        DocumentSighting(
            document_id=document_id,
            source=item.source,
            source_ref=item.source_ref[:400],
            outcome=outcome,
        )
    )


def _clip(value: str | None, n: int) -> str | None:
    return value[:n] if value else value


def ingest(
    session: Session,
    vault: Vault,
    box: SecretBox | None,
    item: Incoming,
    ctx: Context | None = None,
) -> Result:
    prior = session.scalar(
        select(DocumentSighting).where(
            DocumentSighting.source == item.source,
            DocumentSighting.source_ref == item.source_ref[:400],
        )
    )
    if prior is not None:
        return Result("seen", prior.document_id)

    if len(item.data) > MAX_BYTES:
        _sight(session, item, None, "rejected")
        session.commit()
        return Result("rejected", message="Bigger than 40 MB.")

    sha = sha256_of(item.data)
    doc = session.scalar(select(Document).where(Document.sha256 == sha))
    if doc is not None:
        if not vault.has(sha):  # heal a vault that lost a file
            vault.put(item.data)
        _sight(session, item, doc.id, "duplicate")
        session.commit()
        return Result("duplicate", doc.id, doc.status)

    try:
        info = inspect(item.data)
    except NotAPdf as exc:
        _sight(session, item, None, "rejected")
        session.commit()
        return Result("rejected", message=str(exc))

    vault.put(item.data)
    doc = Document(
        sha256=sha,
        size_bytes=len(item.data),
        filename=_clip(item.filename, 255) or "document.pdf",
        source=item.source,
        sender=_clip(item.sender, 255),
        subject=_clip(item.subject, 500),
        received_at=item.received_at,
        encrypted=info.encrypted,
        page_count=info.pages,
        status="needs_review",
        kind="unknown",
    )
    text = info.text
    if info.encrypted:
        if info.text is not None:
            doc.unlocked_with = "empty"
        else:
            found = try_unlock(session, box, item.data, hint_keys(item.sender, item.filename))
            if found:
                doc.unlocked_with, opened = found
                doc.page_count, text = opened.pages, opened.text
    apply_classification(doc, text, ctx or Context.load(session))
    session.add(doc)
    try:
        session.flush()
        _sight(session, item, doc.id, "new")
        session.commit()
    except IntegrityError:
        # Another run stored the same file a moment ago.
        session.rollback()
        existing = session.scalar(select(Document).where(Document.sha256 == sha))
        if existing is None:
            raise
        if not Seen(session, item.source).has(item.source_ref):
            _sight(session, item, existing.id, "duplicate")
            session.commit()
        return Result("duplicate", existing.id, existing.status)
    log.info("Stored document %s (%s, %s)", doc.id, doc.kind, doc.status)
    return Result("new", doc.id, doc.status)


def readable_text(
    session: Session, vault: Vault, box: SecretBox | None, doc: Document
) -> str | None:
    """The document's first-page text, unlocking it now if we can."""
    try:
        data = vault.get(doc.sha256)
    except VaultError as exc:
        log.warning("%s", exc)
        return None
    if not doc.encrypted:
        return inspect(data).text
    if doc.unlocked_with:
        pw = passwords.resolve(session, box, doc.unlocked_with) if box else None
        if doc.unlocked_with == "empty":
            pw = ""
        if pw is not None:
            found = first_that_opens(data, [(doc.unlocked_with, pw)])
            if found:
                return found[1].text
        doc.unlocked_with = None  # the saved password is gone or no longer works
    found = try_unlock(session, box, data, hint_keys(doc.sender, doc.filename, doc.institution))
    if not found:
        return None
    doc.unlocked_with, opened = found
    doc.page_count = opened.pages
    return opened.text


def reprocess(
    session: Session,
    vault: Vault,
    box: SecretBox | None,
    *,
    only_locked: bool = False,
) -> dict[str, int]:
    """Try passwords again and re-run filing rules. Your own manual filing is kept."""
    ctx = Context.load(session)
    q = select(Document).where(Document.status != "ignored")
    if only_locked:
        q = q.where(Document.status == "needs_password")
    counts = {"checked": 0, "unlocked": 0, "changed": 0}
    for doc in session.scalars(q.order_by(Document.id)).all():
        was_locked = doc.status == "needs_password"
        if doc.classified_by == MANUAL and not was_locked:
            continue
        counts["checked"] += 1
        before = (
            doc.kind,
            doc.institution,
            doc.account_id,
            doc.period_start,
            doc.period_end,
            doc.status,
        )
        text = readable_text(session, vault, box, doc)
        apply_classification(doc, text, ctx)
        if was_locked and doc.status != "needs_password":
            counts["unlocked"] += 1
        if before != (
            doc.kind,
            doc.institution,
            doc.account_id,
            doc.period_start,
            doc.period_end,
            doc.status,
        ):
            counts["changed"] += 1
        session.commit()
    return counts
