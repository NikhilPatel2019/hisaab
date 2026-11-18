"""Postbox HTTP API: documents, statement passwords, filing rules and connections."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from hisaab.api import require_user, secret_box
from hisaab.db import get_session
from hisaab.docs import passwords
from hisaab.docs.connectors import (
    CONNECTOR_KINDS,
    Runner,
    check_secret,
    clean_config,
    secret_name,
)
from hisaab.docs.ingest import MANUAL, Context, Incoming, ingest, reprocess, status_for
from hisaab.docs.pdf import decrypted_copy
from hisaab.docs.vault import Vault, VaultError, sha256_of
from hisaab.invest import jobs as invest_jobs
from hisaab.ledger.api import _Patch
from hisaab.models import (
    DOC_KINDS,
    Account,
    Connector,
    DocRule,
    Document,
    DocumentSighting,
)
from hisaab.secretstore import SecretBox, delete_secret, put_secret

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])
Db = Annotated[Session, Depends(get_session)]
Box = Annotated[SecretBox, Depends(secret_box)]

DocKind = Literal[
    "bank_statement",
    "card_statement",
    "cas",
    "broker",
    "tax",
    "payslip",
    "insurance",
    "other",
    "unknown",
]
DocStatus = Literal["needs_password", "needs_review", "filed", "ignored"]
assert set(DocKind.__args__) == set(DOC_KINDS)  # type: ignore[attr-defined]

KIND_GROUPS: dict[str, tuple[str, ...]] = {
    "banks": ("bank_statement",),
    "cards": ("card_statement",),
    "investments": ("cas", "broker"),
    "tax": ("tax", "payslip"),
}


def vault_of(request: Request) -> Vault:
    vault: Vault = request.app.state.vault
    return vault


def runner_of(request: Request) -> Runner:
    runner: Runner = request.app.state.runner
    return runner


def optional_box(request: Request) -> SecretBox | None:
    box: SecretBox | None = request.app.state.secret_box
    return box


def _get(db: Session, model: type[Any], id_: int, what: str) -> Any:
    obj = db.get(model, id_)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No {what} with id {id_}.")
    return obj


def kick_cas_import(request: Request, db: Session) -> None:
    """If the postbox now holds a CAS we haven't read, read it in the background."""
    from hisaab.invest.importer import pending

    if not request.app.state.settings.auto_import_cas or not pending(db):
        return
    factory, vault, box = request.app.state.sessionmaker, vault_of(request), optional_box(request)
    on = datetime.now(ZoneInfo(request.app.state.settings.timezone)).date()
    runner_of(request).start_job(
        "Reading CAS files", lambda: invest_jobs.import_cas(factory, vault, box, on)
    )


# --- documents --------------------------------------------------------------------------------


class DocumentOut(BaseModel):
    id: int
    filename: str
    size_bytes: int
    source: str
    sender: str | None
    subject: str | None
    received_at: datetime | None
    created_at: datetime
    encrypted: bool
    unlocked: bool
    opened_with: str | None
    page_count: int | None
    status: DocStatus
    kind: DocKind
    institution: str | None
    account_id: int | None
    account_name: str | None
    period_start: date | None
    period_end: date | None
    filed_by: Literal["you", "rule", "builtin"] | None
    note: str | None
    copies_seen: int


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    total: int


class Sighting(BaseModel):
    source: str
    outcome: str
    seen_at: datetime


class DocumentDetail(DocumentOut):
    sightings: list[Sighting]


def _filed_by(doc: Document) -> Literal["you", "rule", "builtin"] | None:
    c = doc.classified_by or ""
    if c == MANUAL:
        return "you"
    if c.startswith("rule:"):
        return "rule"
    if c.startswith("builtin:"):
        return "builtin"
    return None


def _doc_out(
    db: Session, d: Document, names: dict[int, str] | None = None, copies: int | None = None
) -> DocumentOut:
    if names is None:
        acct = db.get(Account, d.account_id) if d.account_id else None
        names = {acct.id: acct.name} if acct else {}
    if copies is None:
        copies = (
            db.scalar(
                select(func.count(DocumentSighting.id)).where(DocumentSighting.document_id == d.id)
            )
            or 0
        )
    return DocumentOut(
        id=d.id,
        filename=d.filename,
        size_bytes=d.size_bytes,
        source=d.source,
        sender=d.sender,
        subject=d.subject,
        received_at=d.received_at,
        created_at=d.created_at,
        encrypted=d.encrypted,
        unlocked=not d.encrypted or bool(d.unlocked_with),
        opened_with=passwords.describe(d.unlocked_with) if d.encrypted else None,
        page_count=d.page_count,
        status=d.status,  # type: ignore[arg-type]
        kind=d.kind,  # type: ignore[arg-type]
        institution=d.institution,
        account_id=d.account_id,
        account_name=names.get(d.account_id) if d.account_id else None,
        period_start=d.period_start,
        period_end=d.period_end,
        filed_by=_filed_by(d),
        note=d.note,
        copies_seen=copies,
    )


@router.get("/documents")
def list_documents(
    db: Db,
    status_: Annotated[
        Literal["needs_you", "needs_password", "needs_review", "filed", "ignored"] | None,
        Query(alias="status"),
    ] = None,
    group: Literal["banks", "cards", "investments", "tax"] | None = None,
    kind: DocKind | None = None,
    account_id: int | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentPage:
    query = select(Document)
    if status_ == "needs_you":
        query = query.where(Document.status.in_(("needs_password", "needs_review")))
    elif status_:
        query = query.where(Document.status == status_)
    else:
        query = query.where(Document.status != "ignored")
    if group:
        query = query.where(Document.kind.in_(KIND_GROUPS[group]))
    if kind:
        query = query.where(Document.kind == kind)
    if account_id is not None:
        query = query.where(Document.account_id == account_id)
    if q and q.strip():
        needle = q.strip()
        query = query.where(
            or_(
                Document.filename.contains(needle, autoescape=True),
                Document.sender.contains(needle, autoescape=True),
                Document.subject.contains(needle, autoescape=True),
                Document.institution.contains(needle, autoescape=True),
            )
        )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    order = func.coalesce(
        Document.period_end, func.date(Document.received_at), func.date(Document.created_at)
    )
    docs = list(
        db.scalars(query.order_by(order.desc(), Document.id.desc()).limit(limit).offset(offset))
    )
    ids = [d.id for d in docs]
    copies = (
        dict(
            db.execute(
                select(DocumentSighting.document_id, func.count(DocumentSighting.id))
                .where(DocumentSighting.document_id.in_(ids))
                .group_by(DocumentSighting.document_id)
            ).all()
        )
        if ids
        else {}
    )
    names = dict(db.execute(select(Account.id, Account.name)).all())
    return DocumentPage(
        items=[_doc_out(db, d, names, copies.get(d.id, 0)) for d in docs], total=total
    )


class DocStats(BaseModel):
    kept: int
    duplicates_skipped: int
    filed: int
    filed_automatically: int
    auto_filed_pct: int | None
    needs_password: int
    needs_review: int
    ignored: int
    by_kind: dict[str, int]
    last_document_at: datetime | None


@router.get("/documents/stats")
def document_stats(db: Db) -> DocStats:
    by_status = dict(
        db.execute(select(Document.status, func.count(Document.id)).group_by(Document.status)).all()
    )
    by_kind = dict(
        db.execute(
            select(Document.kind, func.count(Document.id))
            .where(Document.status != "ignored")
            .group_by(Document.kind)
        ).all()
    )
    auto = (
        db.scalar(
            select(func.count(Document.id)).where(
                Document.status == "filed",
                or_(
                    Document.classified_by.like("builtin:%"), Document.classified_by.like("rule:%")
                ),
            )
        )
        or 0
    )
    dupes = (
        db.scalar(
            select(func.count(DocumentSighting.id)).where(DocumentSighting.outcome == "duplicate")
        )
        or 0
    )
    filed = by_status.get("filed", 0)
    review = by_status.get("needs_review", 0)
    # Share of readable documents filed without you. Locked ones aren't counted until opened.
    sortable = filed + review
    return DocStats(
        kept=sum(by_status.values()),
        duplicates_skipped=dupes,
        filed=filed,
        filed_automatically=auto,
        auto_filed_pct=round(100 * auto / sortable) if sortable else None,
        needs_password=by_status.get("needs_password", 0),
        needs_review=review,
        ignored=by_status.get("ignored", 0),
        by_kind=by_kind,
        last_document_at=db.scalar(select(func.max(Document.created_at))),
    )


class UploadResult(BaseModel):
    filename: str
    outcome: Literal["new", "duplicate", "seen", "rejected"]
    document_id: int | None
    status: DocStatus | None
    message: str | None


@router.post("/documents", status_code=status.HTTP_201_CREATED)
def upload_documents(
    request: Request, db: Db, files: Annotated[list[UploadFile], File(description="PDF statements")]
) -> list[UploadResult]:
    from hisaab.docs.ingest import MAX_BYTES

    vault, box = vault_of(request), optional_box(request)
    ctx = Context.load(db)
    out = []
    for f in files[:50]:
        data = f.file.read(MAX_BYTES + 1)
        name = (f.filename or "upload.pdf").replace("\\", "/").rsplit("/", 1)[-1]
        r = ingest(
            db,
            vault,
            box,
            Incoming(data=data, filename=name, source="upload", source_ref=sha256_of(data)),
            ctx,
        )
        status_ = r.status
        if r.outcome == "seen" and r.document_id:
            existing = db.get(Document, r.document_id)
            status_ = existing.status if existing else None
        out.append(
            UploadResult(
                filename=name,
                outcome=r.outcome,
                document_id=r.document_id,
                status=status_,  # type: ignore[arg-type]
                message=r.message
                or ("Already in your postbox." if r.outcome in ("seen", "duplicate") else None),
            )
        )
    kick_cas_import(request, db)
    return out


@router.get("/documents/{doc_id}")
def get_document(db: Db, doc_id: int) -> DocumentDetail:
    d: Document = _get(db, Document, doc_id, "document")
    sightings = db.scalars(
        select(DocumentSighting)
        .where(DocumentSighting.document_id == d.id)
        .order_by(DocumentSighting.seen_at)
    )
    base = _doc_out(db, d)
    return DocumentDetail(
        **base.model_dump(),
        sightings=[
            Sighting(source=s.source, outcome=s.outcome, seen_at=s.seen_at) for s in sightings
        ],
    )


def _file_response(data: bytes, filename: str, mime: str, download: bool) -> Response:
    ascii_name = "".join(
        c if c.isascii() and c.isprintable() and c not in '"\\' else "_" for c in filename
    )
    disposition = "attachment" if download else "inline"
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Content-Disposition": (
                f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
            ),
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/documents/{doc_id}/original", response_class=Response)
def document_original(request: Request, db: Db, doc_id: int, download: bool = False) -> Response:
    """The file exactly as it arrived, still locked if it was."""
    d: Document = _get(db, Document, doc_id, "document")
    try:
        data = vault_of(request).get(d.sha256)
    except VaultError as exc:
        raise HTTPException(status.HTTP_410_GONE, str(exc)) from None
    return _file_response(data, d.filename, d.mime, download)


@router.get("/documents/{doc_id}/unlocked", response_class=Response)
def document_unlocked(request: Request, db: Db, doc_id: int, download: bool = False) -> Response:
    """A copy without the password, made in memory for viewing. Nothing is written to disk."""
    d: Document = _get(db, Document, doc_id, "document")
    try:
        data = vault_of(request).get(d.sha256)
    except VaultError as exc:
        raise HTTPException(status.HTTP_410_GONE, str(exc)) from None
    if not d.encrypted:
        return _file_response(data, d.filename, d.mime, download)
    if not d.unlocked_with:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This file is still locked. Add its password first."
        )
    box = optional_box(request)
    pw = (
        ""
        if d.unlocked_with == "empty"
        else (passwords.resolve(db, box, d.unlocked_with) if box else None)
    )
    if pw is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "The password that opened this file was removed."
        )
    try:
        copy = decrypted_copy(data, pw)
    except PermissionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return _file_response(copy, d.filename, "application/pdf", download)


class DocumentPatch(_Patch):
    not_null = ("kind", "status")
    kind: DocKind | None = None
    status: Literal["filed", "ignored", "needs_review"] | None = None
    institution: str | None = Field(default=None, max_length=100)
    account_id: int | None = None
    period_start: date | None = None
    period_end: date | None = None
    note: str | None = Field(default=None, max_length=500)


@router.patch("/documents/{doc_id}")
def patch_document(request: Request, db: Db, doc_id: int, body: DocumentPatch) -> DocumentOut:
    d: Document = _get(db, Document, doc_id, "document")
    fields = body.model_fields_set
    if "account_id" in fields and body.account_id is not None:
        _get(db, Account, body.account_id, "account")
    for name in ("kind", "institution", "account_id", "period_start", "period_end", "note"):
        if name in fields:
            setattr(d, name, getattr(body, name))
    if fields & {"kind", "institution", "account_id", "period_start", "period_end"}:
        d.classified_by = MANUAL
    if d.period_start and d.period_end and d.period_start > d.period_end:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "The period ends before it starts."
        )
    if "status" in fields:
        d.status = body.status  # type: ignore[assignment]
        if d.status != "ignored":
            d.status = status_for(d)
    elif d.status != "ignored":
        d.status = status_for(d)
    db.commit()
    if d.kind == "cas":
        kick_cas_import(request, db)
    return _doc_out(db, d)


class UnlockBody(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    save_as: str | None = Field(
        default=None,
        max_length=60,
        description="Label for the saved password (it's kept, encrypted, to reopen files)",
    )


class UnlockResult(BaseModel):
    opened: bool
    also_unlocked: int
    document: DocumentOut


@router.post("/documents/{doc_id}/unlock")
def unlock_document(
    request: Request, db: Db, box: Box, doc_id: int, body: UnlockBody
) -> UnlockResult:
    from hisaab.docs.ingest import hint_keys
    from hisaab.docs.pdf import first_that_opens

    d: Document = _get(db, Document, doc_id, "document")
    vault = vault_of(request)
    try:
        data = vault.get(d.sha256)
    except VaultError as exc:
        raise HTTPException(status.HTTP_410_GONE, str(exc)) from None
    if not first_that_opens(data, [("try", body.password)]):
        return UnlockResult(opened=False, also_unlocked=0, document=_doc_out(db, d))
    # Keep the password (encrypted) so this file can be viewed and re-read later.
    name = passwords.add_password(
        db, box, body.save_as or d.institution or "statement", body.password
    )
    cid = "secret:" + name
    passwords.remember(db, hint_keys(d.sender, d.filename, d.institution), cid)
    db.commit()
    counts = reprocess(db, vault, box, only_locked=True)
    db.refresh(d)
    kick_cas_import(request, db)
    return UnlockResult(
        opened=True, also_unlocked=max(0, counts["unlocked"] - 1), document=_doc_out(db, d)
    )


class ReprocessResult(BaseModel):
    checked: int
    unlocked: int
    changed: int


@router.post("/documents/reprocess")
def reprocess_documents(request: Request, db: Db) -> ReprocessResult:
    result = ReprocessResult(**reprocess(db, vault_of(request), optional_box(request)))
    kick_cas_import(request, db)
    return result


# --- statement passwords ----------------------------------------------------------------------


class SavedPassword(BaseModel):
    name: str
    label: str
    created_at: datetime


class PasswordState(BaseModel):
    facts: dict[str, bool]
    saved: list[SavedPassword]
    locked_documents: int
    unlocked_now: int = 0


def _password_state(db: Session, unlocked: int = 0) -> PasswordState:
    return PasswordState(
        facts=passwords.facts_present(db),
        saved=[
            SavedPassword(
                name=s.name, label=s.name.removeprefix(passwords.PW_PREFIX), created_at=s.created_at
            )
            for s in passwords.saved_passwords(db)
        ],
        locked_documents=db.scalar(
            select(func.count(Document.id)).where(Document.status == "needs_password")
        )
        or 0,
        unlocked_now=unlocked,
    )


@router.get("/statement-passwords")
def get_statement_passwords(db: Db) -> PasswordState:
    """Which facts are saved (never their values) and the labels of saved passwords."""
    return _password_state(db)


class FactsBody(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    dob: str | None = Field(default=None, max_length=10, description="YYYY-MM-DD")
    pan: str | None = Field(default=None, max_length=12)
    mobile: str | None = Field(default=None, max_length=20)


@router.put("/statement-passwords/facts")
def put_facts(request: Request, db: Db, box: Box, body: FactsBody) -> PasswordState:
    """Save (or clear, with an empty value) the facts banks build passwords from."""
    for key in body.model_fields_set:
        try:
            passwords.set_fact(db, box, key, getattr(body, key))
        except passwords.FactError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    counts = reprocess(db, vault_of(request), box, only_locked=True)
    kick_cas_import(request, db)
    return _password_state(db, counts["unlocked"])


class PasswordBody(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=200)


@router.post("/statement-passwords", status_code=status.HTTP_201_CREATED)
def add_statement_password(request: Request, db: Db, box: Box, body: PasswordBody) -> PasswordState:
    passwords.add_password(db, box, body.label, body.password)
    counts = reprocess(db, vault_of(request), box, only_locked=True)
    kick_cas_import(request, db)
    return _password_state(db, counts["unlocked"])


@router.delete("/statement-passwords/{name}")
def delete_statement_password(db: Db, name: str) -> PasswordState:
    if not name.startswith(passwords.PW_PREFIX) or not delete_secret(db, name):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No saved password with that name.")
    return _password_state(db)


# --- filing rules -----------------------------------------------------------------------------


class DocRuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    match_sender: str | None = Field(default=None, max_length=255)
    match_filename: str | None = Field(default=None, max_length=255)
    match_text: str | None = Field(default=None, max_length=255)
    kind: DocKind
    institution: str | None = Field(default=None, max_length=100)
    account_id: int | None = None


class DocRuleOut(DocRuleIn):
    id: int
    created_at: datetime


class DocRuleCreated(DocRuleOut):
    reclassified: int


def _rule_out(r: DocRule) -> DocRuleOut:
    return DocRuleOut(
        id=r.id,
        name=r.name,
        match_sender=r.match_sender,
        match_filename=r.match_filename,
        match_text=r.match_text,
        kind=r.kind,  # type: ignore[arg-type]
        institution=r.institution,
        account_id=r.account_id,
        created_at=r.created_at,
    )


@router.get("/doc-rules")
def list_doc_rules(db: Db) -> list[DocRuleOut]:
    return [_rule_out(r) for r in db.scalars(select(DocRule).order_by(DocRule.id))]


@router.post("/doc-rules", status_code=status.HTTP_201_CREATED)
def create_doc_rule(request: Request, db: Db, body: DocRuleIn) -> DocRuleCreated:
    data = body.model_dump()
    for k in ("match_sender", "match_filename", "match_text", "institution"):
        data[k] = (data[k] or "").strip() or None
    if not (data["match_sender"] or data["match_filename"] or data["match_text"]):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Say what to match: sender, file name or text."
        )
    if data["account_id"] is not None:
        _get(db, Account, data["account_id"], "account")
    rule = DocRule(**data)
    db.add(rule)
    db.commit()
    counts = reprocess(db, vault_of(request), optional_box(request))
    return DocRuleCreated(**_rule_out(rule).model_dump(), reclassified=counts["changed"])


@router.delete("/doc-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_doc_rule(db: Db, rule_id: int) -> None:
    db.delete(_get(db, DocRule, rule_id, "rule"))
    db.commit()


# --- connections ------------------------------------------------------------------------------

ConnectorKind = Literal["folder", "drive", "imap"]
assert set(ConnectorKind.__args__) == set(CONNECTOR_KINDS)  # type: ignore[attr-defined]


class ConnectorOut(BaseModel):
    id: int
    kind: ConnectorKind
    name: str
    config: dict[str, Any]
    enabled: bool
    has_secret: bool
    last_run_at: datetime | None
    last_ok_at: datetime | None
    last_error: str | None
    last_summary: str | None


class ConnectorIn(BaseModel):
    kind: ConnectorKind
    name: str = Field(min_length=1, max_length=100)
    config: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = Field(
        default=None, max_length=20000, description="App password or service-account JSON"
    )
    enabled: bool = True


class ConnectorPatch(_Patch):
    not_null = ("name", "config", "enabled")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    config: dict[str, Any] | None = None
    secret: str | None = Field(default=None, max_length=20000)
    enabled: bool | None = None


class RunnerState(BaseModel):
    running: bool
    current: str | None
    started_at: datetime | None


def _conn_out(db: Session, c: Connector) -> ConnectorOut:
    from hisaab.models import Secret

    return ConnectorOut(
        id=c.id,
        kind=c.kind,  # type: ignore[arg-type]
        name=c.name,
        config=json.loads(c.config or "{}"),
        enabled=c.enabled,
        has_secret=db.get(Secret, secret_name(c.id)) is not None,
        last_run_at=c.last_run_at,
        last_ok_at=c.last_ok_at,
        last_error=c.last_error,
        last_summary=c.last_summary,
    )


def _save_secret(db: Session, request: Request, c: Connector, secret: str | None) -> None:
    if secret is None:
        return
    if not secret.strip():
        delete_secret(db, secret_name(c.id))
        return
    try:
        extra = check_secret(c.kind, secret)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    if c.kind == "imap":
        secret = secret.strip()
        if re.fullmatch(r"[A-Za-z]{4}( [A-Za-z]{4}){3}", secret):
            secret = secret.replace(" ", "")  # Google shows app passwords in groups of four
    put_secret(db, secret_box(request), secret_name(c.id), secret)
    if extra:
        c.config = json.dumps({**json.loads(c.config or "{}"), **extra})


@router.get("/connectors")
def list_connectors(db: Db) -> list[ConnectorOut]:
    return [_conn_out(db, c) for c in db.scalars(select(Connector).order_by(Connector.id))]


@router.get("/connectors/status")
def connectors_status(request: Request) -> RunnerState:
    r = runner_of(request)
    return RunnerState(running=r.running, current=r.current, started_at=r.started_at)


@router.post("/connectors", status_code=status.HTTP_201_CREATED)
def create_connector(request: Request, db: Db, body: ConnectorIn) -> ConnectorOut:
    try:
        cfg = clean_config(body.kind, body.config)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    if body.kind != "folder" and body.secret:
        secret_box(request)  # fail early, before creating anything, if secrets are off
    c = Connector(
        kind=body.kind, name=body.name.strip(), config=json.dumps(cfg), enabled=body.enabled
    )
    db.add(c)
    db.flush()
    try:
        _save_secret(db, request, c, body.secret if body.kind != "folder" else None)
    except HTTPException:
        db.rollback()
        raise
    db.commit()
    return _conn_out(db, c)


@router.patch("/connectors/{conn_id}")
def patch_connector(request: Request, db: Db, conn_id: int, body: ConnectorPatch) -> ConnectorOut:
    c: Connector = _get(db, Connector, conn_id, "connection")
    fields = body.model_fields_set
    if "config" in fields and body.config is not None:
        try:
            keep = {
                k: v
                for k, v in json.loads(c.config or "{}").items()
                if k == "service_account_email"
            }
            c.config = json.dumps({**keep, **clean_config(c.kind, body.config)})
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    if "name" in fields and body.name:
        c.name = body.name.strip()
    if "enabled" in fields and body.enabled is not None:
        c.enabled = body.enabled
    if "secret" in fields and c.kind != "folder":
        _save_secret(db, request, c, body.secret or "")
    db.commit()
    return _conn_out(db, c)


@router.delete("/connectors/{conn_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connector(db: Db, conn_id: int) -> None:
    c: Connector = _get(db, Connector, conn_id, "connection")
    delete_secret(db, secret_name(c.id))
    db.delete(c)
    db.commit()


def _start(request: Request, ids: list[int], wait: bool) -> RunnerState:
    runner = runner_of(request)
    if not ids:
        raise HTTPException(status.HTTP_409_CONFLICT, "No connections are switched on.")
    if not runner.start(ids, wait=wait):
        raise HTTPException(status.HTTP_409_CONFLICT, "A sync is already running.")
    return RunnerState(running=runner.running, current=runner.current, started_at=runner.started_at)


@router.post("/connectors/{conn_id}/run", status_code=status.HTTP_202_ACCEPTED)
def run_one(request: Request, db: Db, conn_id: int, wait: bool = False) -> RunnerState:
    c: Connector = _get(db, Connector, conn_id, "connection")
    return _start(request, [c.id], wait)


@router.post("/connectors/run", status_code=status.HTTP_202_ACCEPTED)
def run_all(request: Request, db: Db, wait: bool = False) -> RunnerState:
    ids = list(
        db.scalars(select(Connector.id).where(Connector.enabled.is_(True)).order_by(Connector.id))
    )
    return _start(request, ids, wait)
