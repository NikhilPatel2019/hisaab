"""Postbox end to end: backfills, re-runs, connectors, the API, and keeping passwords secret."""

from __future__ import annotations

import io
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from hisaab.docs.connectors import ConnectorError, DriveSource, FolderSource, ImapSource, run_source
from hisaab.docs.pdf import inspect
from hisaab.models import Base, Document, DocumentSighting

from .conftest import CSRF
from .pdfs import bank_statement, card_statement, cas, lock, make_pdf

# Every value below is made up.
NAME, DOB, PAN = "Asha Verma", "1990-03-07", "ABCDE1234F"
BANK_PW = "ASHA0703"  # name4 + DDMM
CARD_PW = "asha9876"  # name4 (lower) + card's last 4
CAS_PW = PAN
ONE_OFF_PW = "Zebra-Plum-42"  # a password no recipe could guess
SECRETS = (BANK_PW, CARD_PW, ONE_OFF_PW)


def corpus() -> dict[str, tuple[bytes, str]]:
    """24 months of statements: filename -> (bytes, sender)."""
    files: dict[str, tuple[bytes, str]] = {}
    for i in range(24):
        year, month = 2024 + (i + 10) // 12, (i + 10) % 12 + 1  # Nov 2024 … Oct 2026
        files[f"bank_{year}_{month:02d}.pdf"] = (
            lock(make_pdf(bank_statement(month, year)), BANK_PW),
            "Bank Alerts <emailstatements.hdfcbank@hdfcbank.net>",
        )
        files[f"card_{year}_{month:02d}.pdf"] = (
            lock(make_pdf(card_statement(month, year)), CARD_PW),
            "Statements <statements@sbicard.com>",
        )
        cas_pdf = make_pdf(cas(month, year))
        files[f"cas_{year}_{month:02d}.pdf"] = (
            lock(cas_pdf, CAS_PW) if i % 2 else cas_pdf,
            "eCAS <ecas@cdslstatement.com>",
        )
    return files


def _setup_accounts_and_facts(c: TestClient) -> None:
    for body in (
        {"name": "Salary", "kind": "bank", "institution": "HDFC Bank", "mask": "4321"},
        {"name": "Rewards card", "kind": "credit_card", "institution": "SBI Card", "mask": "9876"},
    ):
        assert c.post("/api/accounts", json=body, headers=CSRF).status_code == 201
    r = c.put(
        "/api/statement-passwords/facts", json={"name": NAME, "dob": DOB, "pan": PAN}, headers=CSRF
    )
    assert r.status_code == 200, r.text
    assert r.json()["facts"] == {"name": True, "dob": True, "pan": True, "mobile": False}
    assert NAME not in r.text and PAN not in r.text


def _assert_no_secret_anywhere(
    c: TestClient, caplog: pytest.LogCaptureFixture, extra: list[str]
) -> None:
    app = c.app
    with app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        for table in Base.metadata.sorted_tables:
            for row in s.execute(table.select()).all():
                blob = " ".join(str(v) for v in row)
                for pw in (*SECRETS, *extra):
                    assert pw not in blob, f"{pw!r} found in {table.name}"
    for path in Path(app.state.vault.root).rglob("*.bin"):  # type: ignore[attr-defined]
        data = path.read_bytes()
        for pw in (*SECRETS, *extra):
            assert pw.encode() not in data
    for pw in (*SECRETS, *extra):
        assert pw not in caplog.text


class FakeAtt:
    def __init__(
        self, filename: str, payload: bytes, content_type: str = "application/pdf"
    ) -> None:
        self.filename, self.payload, self.content_type = filename, payload, content_type


class FakeMsg:
    def __init__(self, uid: str, mid: str, sender: str, subject: str, atts: list[FakeAtt]) -> None:
        self.uid, self.from_, self.subject, self.attachments = uid, sender, subject, atts
        self.headers = {"message-id": (mid,)}
        self.date = datetime(2026, 1, 1, tzinfo=UTC)


class FakeMailBox:
    """Enough of imap_tools.MailBox for the connector."""

    def __init__(self, messages: list[FakeMsg], password: str) -> None:
        self.messages, self.password = messages, password
        self.full_fetches = 0
        self.criteria: list[str] = []

    def __call__(self, host: str, port: int) -> FakeMailBox:
        return self

    def login(self, user: str, password: str, initial_folder: str = "INBOX") -> None:
        if password != self.password:
            raise RuntimeError(
                f"[AUTHENTICATIONFAILED] Invalid credentials for {user} / {password}"
            )

    def fetch(
        self, criteria: Any, headers_only: bool = False, mark_seen: bool = True, bulk: Any = False
    ) -> Any:
        crit = str(criteria)
        self.criteria.append(crit)
        m = re.search(r"UID (\S+?)\)?$", crit)
        if m:
            self.full_fetches += 1
            return [x for x in self.messages if x.uid == m.group(1)]
        return list(self.messages)

    def logout(self) -> None:
        pass


class FakeResp:
    def __init__(self, status: int = 200, body: Any = None, content: bytes = b"") -> None:
        self.status_code, self._body, self.content = status, body, content

    def json(self) -> Any:
        return self._body


class FakeDrive:
    """files: id -> (name, [(revision id, modified time, bytes), …])."""

    def __init__(
        self,
        folder: str,
        files: dict[str, tuple[str, list[tuple[str, str, bytes]]]],
        status: int = 200,
    ) -> None:
        self.folder, self.files, self.status = folder, files, status
        self.downloads: list[str] = []

    def get(self, url: str, params: dict[str, Any] | None = None, timeout: float = 0) -> FakeResp:
        if self.status != 200:
            return FakeResp(self.status)
        if url.endswith("/files"):
            assert params and f"'{self.folder}' in parents" in params["q"]
            return FakeResp(
                body={
                    "files": [
                        {
                            "id": fid,
                            "name": name,
                            "mimeType": "application/pdf",
                            "createdTime": "2026-01-02T03:04:05Z",
                        }
                        for fid, (name, _) in self.files.items()
                    ]
                }
            )
        m = re.search(r"/files/([^/]+)/revisions/([^/]+)$", url)
        if m:
            self.downloads.append(f"{m.group(1)}@{m.group(2)}")
            revs = self.files[m.group(1)][1]
            return FakeResp(content=next(b for rid, _, b in revs if rid == m.group(2)))
        m = re.search(r"/files/([^/]+)/revisions$", url)
        if m:
            revs = self.files[m.group(1)][1]
            # newest first, to check we sort rather than trust the order
            return FakeResp(
                body={"revisions": [{"id": rid, "modifiedTime": t} for rid, t, _ in reversed(revs)]}
            )
        raise AssertionError(url)


# --- the acceptance test ----------------------------------------------------------------------


def test_backfill_stores_each_pdf_once_and_reruns_add_nothing(
    logged_in: TestClient, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    c = logged_in
    _setup_accounts_and_facts(c)
    files = corpus()
    junk = make_pdf(["Your movie ticket", "Screen 3"])

    folder = tmp_path / "inbox"
    folder.mkdir()
    for name, (data, _) in files.items():
        (folder / name).write_bytes(data)
    (folder / "ticket.pdf").write_bytes(junk)
    (folder / "notes.txt").write_text("not a pdf")
    (folder / "fake.pdf").write_bytes(b"this is not really a pdf")

    r = c.post(
        "/api/connectors",
        json={"kind": "folder", "name": "Inbox folder", "config": {"path": str(folder)}},
        headers=CSRF,
    )
    assert r.status_code == 201, r.text
    conn_id = r.json()["id"]
    r = c.post(f"/api/connectors/{conn_id}/run?wait=true", headers=CSRF)
    assert r.status_code == 202, r.text

    conn = c.get("/api/connectors").json()[0]
    assert conn["last_error"] is None and conn["last_summary"].startswith(
        f"{len(files) + 1} new"
    ), conn

    stats = c.get("/api/documents/stats").json()
    assert stats["kept"] == len(files) + 1
    assert stats["needs_password"] == 0
    assert stats["auto_filed_pct"] >= 95
    assert stats["by_kind"] == {"bank_statement": 24, "card_statement": 24, "cas": 24, "unknown": 1}

    # Each file is in the vault exactly once, byte for byte.
    vault_files = list(Path(c.app.state.vault.root).rglob("*.bin"))  # type: ignore[attr-defined]
    assert len(vault_files) == len(files) + 1
    docs = c.get("/api/documents?limit=500").json()["items"]
    by_name = {d["filename"]: d for d in docs}
    for name in ("bank_2025_03.pdf", "card_2026_10.pdf", "cas_2025_06.pdf"):
        r = c.get(f"/api/documents/{by_name[name]['id']}/original")
        assert r.status_code == 200 and r.content == files[name][0]
        assert r.headers["cache-control"] == "private, no-store"
    bank = by_name["bank_2025_03.pdf"]
    assert bank["kind"] == "bank_statement" and bank["account_name"] == "Salary"
    assert bank["period_start"] == "2025-03-01" and bank["opened_with"] == "name + date of birth"
    card = by_name["card_2025_03.pdf"]
    assert card["account_name"] == "Rewards card" and card["opened_with"] == "name + card digits"
    unlocked = c.get(f"/api/documents/{bank['id']}/unlocked")
    assert unlocked.status_code == 200 and not inspect(unlocked.content).encrypted

    # Running again finds nothing new.
    assert c.post(f"/api/connectors/{conn_id}/run?wait=true", headers=CSRF).status_code == 202
    assert c.get("/api/documents/stats").json()["kept"] == len(files) + 1
    assert c.get("/api/connectors").json()[0]["last_summary"] == "0 new"

    # The same statements arriving by mail are recognised as duplicates.
    with c.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        msgs = [
            FakeMsg(
                str(i), f"<m{i}@bank.example>", sender, f"Statement {name}", [FakeAtt(name, data)]
            )
            for i, (name, (data, sender)) in enumerate(list(files.items())[:30])
        ]
        msgs.append(
            FakeMsg(
                "999",
                "<promo@shop.example>",
                "offers@shop.example",
                "Sale!",
                [FakeAtt("sale.pdf", junk)],
            )
        )
        mailbox = FakeMailBox(msgs, "app-password-1")
        src = ImapSource(
            host="imap.gmail.com",
            user="me@example.com",
            password="app-password-1",
            mailbox_factory=mailbox,
        )
        first = run_source(s, c.app.state.vault, c.app.state.secret_box, src)  # type: ignore[attr-defined]
        assert (first.new, first.duplicate) == (0, 30)
        assert mailbox.full_fetches == 30  # the unknown sender was never downloaded
        assert 'X-GM-RAW "has:attachment filename:pdf' in mailbox.criteria[0]
        again = run_source(s, c.app.state.vault, c.app.state.secret_box, src)  # type: ignore[attr-defined]
        assert (again.new, again.duplicate, mailbox.full_fetches) == (0, 0, 30)
        assert s.scalar(select(func.count(Document.id))) == len(files) + 1

    assert c.get("/api/documents/stats").json()["duplicates_skipped"] == 30
    _assert_no_secret_anywhere(c, caplog, [NAME, DOB])


# --- passwords --------------------------------------------------------------------------------


def test_locked_files_wait_for_a_password_then_open(
    logged_in: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    c = logged_in
    locked = lock(make_pdf(bank_statement(5, 2026)), ONE_OFF_PW)
    r = c.post(
        "/api/documents",
        files=[("files", ("may.pdf", io.BytesIO(locked), "application/pdf"))],
        headers=CSRF,
    )
    assert r.status_code == 201, r.text
    (res,) = r.json()
    assert res["outcome"] == "new" and res["status"] == "needs_password"
    doc_id = res["document_id"]
    assert c.get(f"/api/documents/{doc_id}/unlocked").status_code == 409
    assert c.get("/api/documents?status=needs_you").json()["total"] == 1

    wrong = c.post(f"/api/documents/{doc_id}/unlock", json={"password": "guess-1"}, headers=CSRF)
    assert wrong.status_code == 200 and wrong.json()["opened"] is False
    assert "guess-1" not in wrong.text

    # A second file with the same password is waiting too; unlocking one opens both.
    other = lock(make_pdf(bank_statement(6, 2026)), ONE_OFF_PW)
    c.post(
        "/api/documents",
        files=[("files", ("june.pdf", io.BytesIO(other), "application/pdf"))],
        headers=CSRF,
    )
    ok = c.post(
        f"/api/documents/{doc_id}/unlock",
        json={"password": ONE_OFF_PW, "save_as": "Old bank"},
        headers=CSRF,
    )
    body = ok.json()
    assert body["opened"] is True and body["also_unlocked"] == 1, body
    assert (
        body["document"]["status"] == "filed"
        and body["document"]["opened_with"] == "saved password “old-bank”"
    )
    assert ONE_OFF_PW not in ok.text

    # Saving the same password again reuses the stored one.
    c.post(
        "/api/statement-passwords",
        json={"label": "Duplicate", "password": ONE_OFF_PW},
        headers=CSRF,
    )
    state = c.get("/api/statement-passwords").json()
    assert [p["label"] for p in state["saved"]] == ["old-bank"] and state["locked_documents"] == 0
    assert ONE_OFF_PW not in str(state)
    assert c.get(f"/api/documents/{doc_id}/unlocked").status_code == 200

    # Removing the password keeps the original; the unlocked view says why it can't open.
    assert c.delete("/api/statement-passwords/pdf.pw.old-bank", headers=CSRF).status_code == 200
    assert c.get(f"/api/documents/{doc_id}/unlocked").status_code == 409
    assert c.get(f"/api/documents/{doc_id}/original").content == locked
    _assert_no_secret_anywhere(c, caplog, ["guess-1"])


def test_adding_facts_unlocks_waiting_files(logged_in: TestClient) -> None:
    c = logged_in
    locked = lock(make_pdf(cas(1, 2026)), PAN)
    c.post(
        "/api/documents",
        files=[("files", ("cas.pdf", io.BytesIO(locked), "application/pdf"))],
        headers=CSRF,
    )
    r = c.put("/api/statement-passwords/facts", json={"pan": PAN.lower()}, headers=CSRF)
    assert r.json()["unlocked_now"] == 1 and r.json()["locked_documents"] == 0
    doc = c.get("/api/documents").json()["items"][0]
    assert (doc["kind"], doc["institution"], doc["opened_with"]) == ("cas", "CDSL", "PAN")
    bad = c.put("/api/statement-passwords/facts", json={"pan": "nope"}, headers=CSRF)
    assert bad.status_code == 422 and "nope" not in bad.text


# --- uploads, filing and rules ----------------------------------------------------------------


def test_upload_dedupes_and_manual_filing_sticks(logged_in: TestClient) -> None:
    c = logged_in
    pdf = make_pdf(["Quarterly letter", "Nothing to classify here"])
    up = lambda name, data: c.post(  # noqa: E731
        "/api/documents",
        files=[("files", (name, io.BytesIO(data), "application/pdf"))],
        headers=CSRF,
    ).json()[0]
    first = up("letter.pdf", pdf)
    assert first["outcome"] == "new" and first["status"] == "needs_review"
    again = up("copy-of-letter.pdf", pdf)
    assert again["outcome"] == "seen" and again["document_id"] == first["document_id"]
    rejected = up("evil.pdf", b"<html>not a pdf</html>")
    assert rejected["outcome"] == "rejected"

    doc_id = first["document_id"]
    r = c.patch(
        f"/api/documents/{doc_id}",
        json={"kind": "insurance", "institution": "Made-up Insurance"},
        headers=CSRF,
    )
    assert r.status_code == 200 and r.json()["status"] == "filed" and r.json()["filed_by"] == "you"
    assert c.patch(f"/api/documents/{doc_id}", json={"kind": None}, headers=CSRF).status_code == 422

    # A rule doesn't override your own filing, but does file other matching documents.
    other = up("renewal_2026.pdf", make_pdf(["Your renewal", "Thanks"]))
    assert other["status"] == "needs_review"
    rule = c.post(
        "/api/doc-rules",
        json={
            "name": "Renewals",
            "match_filename": "renewal_*",
            "kind": "insurance",
            "institution": "Rule Insurance",
        },
        headers=CSRF,
    )
    assert rule.status_code == 201 and rule.json()["reclassified"] == 1
    docs = {d["id"]: d for d in c.get("/api/documents").json()["items"]}
    assert docs[doc_id]["institution"] == "Made-up Insurance"
    assert docs[other["document_id"]]["filed_by"] == "rule"
    assert (
        c.post("/api/doc-rules", json={"name": "Empty", "kind": "tax"}, headers=CSRF).status_code
        == 422
    )

    assert (
        c.patch(f"/api/documents/{doc_id}", json={"status": "ignored"}, headers=CSRF).json()[
            "status"
        ]
        == "ignored"
    )
    assert doc_id not in {d["id"] for d in c.get("/api/documents").json()["items"]}
    detail = c.get(f"/api/documents/{doc_id}").json()
    assert [s["outcome"] for s in detail["sightings"]] == ["new"]


# --- connectors -------------------------------------------------------------------------------


def test_connector_secrets_are_write_only(logged_in: TestClient) -> None:
    c = logged_in
    r = c.post(
        "/api/connectors",
        json={
            "kind": "imap",
            "name": "Gmail",
            "config": {"user": "me@example.com"},
            "secret": "abcd efgh ijkl mnop",
        },
        headers=CSRF,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["has_secret"] is True and body["config"]["host"] == "imap.gmail.com"
    assert "abcd" not in r.text and "abcd" not in c.get("/api/connectors").text

    key = '{"type": "service_account", "client_email": "reader@demo-project.iam.example", "private_key": "-----BEGIN PRIVATE KEY-----\\nZmFrZQ==\\n-----END PRIVATE KEY-----\\n"}'
    d = c.post(
        "/api/connectors",
        json={
            "kind": "drive",
            "name": "Drive",
            "config": {"folder_id": "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp"},
            "secret": key,
        },
        headers=CSRF,
    ).json()
    assert d["config"] == {
        "folder_id": "1AbCdEfGhIjKlMnOp",
        "recursive": True,
        "service_account_email": "reader@demo-project.iam.example",
    }
    assert "PRIVATE" not in c.get("/api/connectors").text

    # A broken key is reported on the connection, without echoing it.
    c.post(f"/api/connectors/{d['id']}/run?wait=true", headers=CSRF)
    drive = next(x for x in c.get("/api/connectors").json() if x["id"] == d["id"])
    assert "service-account key" in drive["last_error"] and "PRIVATE" not in drive["last_error"]

    bad = c.post(
        "/api/connectors",
        json={
            "kind": "drive",
            "name": "x",
            "config": {"folder_id": "1AbCdEfGhIjKlMnOp"},
            "secret": "not json",
        },
        headers=CSRF,
    )
    assert bad.status_code == 422
    assert (
        c.post(
            "/api/connectors",
            json={"kind": "imap", "name": "x", "config": {"user": "nope"}},
            headers=CSRF,
        ).status_code
        == 422
    )

    c.patch(f"/api/connectors/{body['id']}", json={"enabled": False}, headers=CSRF)
    c.patch(f"/api/connectors/{d['id']}", json={"enabled": False}, headers=CSRF)
    assert c.post("/api/connectors/run", headers=CSRF).status_code == 409
    assert c.delete(f"/api/connectors/{body['id']}", headers=CSRF).status_code == 204
    with c.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        assert (
            s.execute(text("SELECT count(*) FROM secrets WHERE name LIKE 'conn.%'")).scalar() == 1
        )


def test_imap_login_failure_never_echoes_the_password(
    logged_in: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    c = logged_in
    src = ImapSource(
        host="imap.example.com",
        user="me@example.com",
        password="wrong-app-pw",
        mailbox_factory=FakeMailBox([], "right"),
    )
    with c.app.state.sessionmaker() as s, pytest.raises(ConnectorError) as err:  # type: ignore[attr-defined]
        run_source(s, c.app.state.vault, c.app.state.secret_box, src)  # type: ignore[attr-defined]
    assert "wrong-app-pw" not in str(err.value) and err.value.__cause__ is None
    assert "wrong-app-pw" not in caplog.text and "wrong-app-pw" not in repr(src)


def test_imap_takes_every_pdf_attachment_once(logged_in: TestClient) -> None:
    c = logged_in
    a, b = make_pdf(cas(1, 2026)), make_pdf(cas(2, 2026))
    msgs = [
        FakeMsg(
            "1",
            "<x1@cdsl.example>",
            "ecas@cdslstatement.com",
            "CAS",
            [
                FakeAtt("a.pdf", a),
                FakeAtt("logo.png", b"\x89PNG", "image/png"),
                FakeAtt("b.PDF", b),
            ],
        ),
        FakeMsg("2", "<x2@cdsl.example>", "ecas@cdslstatement.com", "No attachment", []),
    ]
    box = FakeMailBox(msgs, "pw")
    with c.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        src = ImapSource(
            host="mail.example.com", user="me@example.com", password="pw", mailbox_factory=box
        )
        assert run_source(s, c.app.state.vault, c.app.state.secret_box, src).new == 2  # type: ignore[attr-defined]
        assert run_source(s, c.app.state.vault, c.app.state.secret_box, src).new == 0  # type: ignore[attr-defined]
        assert box.full_fetches == 2  # both messages fetched once, then skipped by message id
        refs = set(s.scalars(select(DocumentSighting.source_ref)))
        assert refs == {
            "<x1@cdsl.example>#0:a.pdf",
            "<x1@cdsl.example>#2:b.PDF",
            "<x1@cdsl.example>#done",
            "<x2@cdsl.example>#done",
        }
        assert not box.criteria[0].startswith("X-GM-RAW")


def test_drive_keeps_the_oldest_revision(logged_in: TestClient) -> None:
    c = logged_in
    original = lock(make_pdf(card_statement(2, 2026)), "drive-pw-9")
    unlocked = make_pdf(card_statement(2, 2026))
    drive = FakeDrive(
        "1FolderIdAbCdEf",
        {
            "1FileAAAAAAAAAA": (
                "card_feb.pdf",
                [
                    ("r1", "2026-03-01T00:00:00Z", original),
                    ("r2", "2026-03-02T00:00:00Z", unlocked),
                ],
            ),
            "1FileBBBBBBBBBB": (
                "cas_feb.pdf",
                [("r9", "2026-03-03T00:00:00Z", make_pdf(cas(2, 2026)))],
            ),
        },
    )
    with c.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        src = DriveSource("1FolderIdAbCdEf", http=drive)
        first = run_source(s, c.app.state.vault, c.app.state.secret_box, src)  # type: ignore[attr-defined]
        assert first.new == 2 and drive.downloads == ["1FileAAAAAAAAAA@r1", "1FileBBBBBBBBBB@r9"]
        assert run_source(s, c.app.state.vault, c.app.state.secret_box, src).new == 0  # type: ignore[attr-defined]
        assert len(drive.downloads) == 2  # nothing downloaded twice
        doc = s.scalar(select(Document).where(Document.filename == "card_feb.pdf"))
        assert doc is not None and doc.encrypted and doc.status == "needs_password"
        assert c.app.state.vault.get(doc.sha256) == original  # type: ignore[attr-defined]

    with c.app.state.sessionmaker() as s, pytest.raises(ConnectorError, match="Share the folder"):  # type: ignore[attr-defined]
        run_source(
            s,
            c.app.state.vault,
            c.app.state.secret_box,
            DriveSource("1FolderIdAbCdEf", http=FakeDrive("x", {}, status=403)),
        )  # type: ignore[attr-defined]


def test_folder_source_reports_a_missing_folder(tmp_path: Path, logged_in: TestClient) -> None:
    with (
        logged_in.app.state.sessionmaker() as s,
        pytest.raises(ConnectorError, match="doesn't exist"),
    ):  # type: ignore[attr-defined]
        run_source(s, logged_in.app.state.vault, None, FolderSource(str(tmp_path / "nope")))  # type: ignore[attr-defined]


def test_cli_ingest_folder(
    logged_in: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from hisaab.cli import main
    from hisaab.config import get_settings

    settings = logged_in.app.state.settings  # type: ignore[attr-defined]
    monkeypatch.setenv("HISAAB_DATABASE_URL", settings.database_url)
    monkeypatch.setenv("HISAAB_VAULT_DIR", settings.vault_dir)
    monkeypatch.setenv("HISAAB_ENV_FILE", str(tmp_path / "none.env"))
    folder = tmp_path / "pdfs"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(make_pdf(cas(3, 2026)))
    get_settings.cache_clear()
    monkeypatch.setattr("hisaab.cli.get_settings", get_settings)
    assert main(["ingest", "--folder", str(folder)]) == 0
    assert "1 new" in capsys.readouterr().out
    assert main(["ingest", "--folder", str(folder)]) == 0
    assert "0 new" in capsys.readouterr().out
    assert main(["ingest"]) == 0  # no connections yet: says so, doesn't fail
    get_settings.cache_clear()
