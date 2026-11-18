"""Where documents come from: a folder on the server, a Google Drive folder, or a mailbox.

Each source yields `Incoming` items and asks `Seen` first, so a re-run only downloads what's
new. Secrets (app passwords, service-account keys) come from the encrypted secret store and
never appear in errors or logs.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.orm import Session, sessionmaker

from hisaab.docs.classify import is_known_sender
from hisaab.docs.ingest import Context, Incoming, Seen, Summary, ingest
from hisaab.docs.vault import Vault
from hisaab.models import Connector
from hisaab.secretstore import SecretBox, get_secret

log = logging.getLogger("hisaab.docs")

CONNECTOR_KINDS = ("folder", "drive", "imap")
DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
FOLDER_MIME = "application/vnd.google-apps.folder"
_DRIVE_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_DOMAIN = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")


class ConnectorError(Exception):
    """A problem the person can fix. The message is shown as-is, so it never holds secrets."""


class Source(Protocol):
    kind: str

    def items(self, seen: Seen) -> Iterator[Incoming]: ...


def secret_name(connector_id: int) -> str:
    return f"conn.{connector_id}.secret"


# --- config validation ------------------------------------------------------------------------


def clean_config(kind: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalise a connector's settings. Raises ValueError with a friendly message."""
    if kind == "folder":
        path = str(raw.get("path") or "").strip()
        if not path:
            raise ValueError("Give the folder's path on the server.")
        return {"path": path, "recursive": bool(raw.get("recursive", True))}
    if kind == "drive":
        folder_id = str(raw.get("folder_id") or "").strip()
        m = re.search(r"/folders/([A-Za-z0-9_-]+)", folder_id)
        if m:
            folder_id = m.group(1)
        if not _DRIVE_ID.match(folder_id):
            raise ValueError("Paste the Drive folder's link or its id.")
        out: dict[str, Any] = {
            "folder_id": folder_id,
            "recursive": bool(raw.get("recursive", True)),
        }
        if raw.get("service_account_email"):
            out["service_account_email"] = str(raw["service_account_email"])[:200]
        return out
    if kind == "imap":
        host = str(raw.get("host") or "imap.gmail.com").strip().lower()
        user = str(raw.get("user") or "").strip()
        if not _HOST.match(host):
            raise ValueError("That mail server name doesn't look right.")
        if "@" not in user:
            raise ValueError("Give the full email address you log in with.")
        try:
            port = int(raw.get("port") or 993)
            since_months = int(raw.get("since_months") or 24)
        except (TypeError, ValueError) as exc:
            raise ValueError("Port and months should be whole numbers.") from exc
        if not 1 <= port <= 65535 or not 1 <= since_months <= 120:
            raise ValueError("Port or months out of range.")
        extra = raw.get("extra_senders") or []
        if isinstance(extra, str):
            extra = re.split(r"[\s,]+", extra)
        extra_clean = []
        for d in extra:
            d = str(d).strip().lower().lstrip("@")
            if d and not _DOMAIN.match(d):
                raise ValueError(f"“{d[:40]}” isn't a domain like bank.example.")
            if d:
                extra_clean.append(d)
        return {
            "host": host,
            "port": port,
            "user": user[:200],
            "folder": str(raw.get("folder") or "INBOX")[:200],
            "since_months": since_months,
            "known_senders_only": bool(raw.get("known_senders_only", True)),
            "extra_senders": extra_clean[:50],
        }
    raise ValueError("Unknown connection type.")


def check_secret(kind: str, secret: str) -> dict[str, Any]:
    """Validate a secret before storing it; returns safe facts about it for the config."""
    if kind == "drive":
        try:
            info = json.loads(secret)
        except ValueError as exc:
            raise ValueError(
                "The service-account key should be the JSON file Google gave you."
            ) from exc
        if (
            not isinstance(info, dict)
            or info.get("type") != "service_account"
            or not info.get("private_key")
        ):
            raise ValueError("That JSON isn't a service-account key.")
        return {"service_account_email": str(info.get("client_email", ""))[:200]}
    if kind == "imap" and not secret.strip():
        raise ValueError("Paste the app password.")
    return {}


# --- sources ----------------------------------------------------------------------------------


class FolderSource:
    kind = "folder"

    def __init__(self, path: str, recursive: bool = True) -> None:
        self.root = Path(path).expanduser()
        self.recursive = recursive

    def items(self, seen: Seen) -> Iterator[Incoming]:
        if not self.root.is_dir():
            raise ConnectorError(f"The folder {self.root} doesn't exist on this server.")
        files = self.root.rglob("*") if self.recursive else self.root.glob("*")
        for path in sorted(files):
            if not path.is_file() or path.suffix.lower() != ".pdf":
                continue
            st = path.stat()
            ref = f"{path.resolve().as_posix()}|{st.st_size}|{st.st_mtime_ns}"
            if seen.has(ref):
                continue
            yield Incoming(
                data=path.read_bytes(),
                filename=path.name,
                source=self.kind,
                source_ref=ref,
                received_at=datetime.fromtimestamp(st.st_mtime, UTC),
            )


class _Http(Protocol):
    def get(self, url: str, params: dict[str, Any] | None = None, timeout: float = ...) -> Any: ...


class DriveSource:
    """A Google Drive folder, read with a service account the folder is shared with.

    If another tool replaced a locked PDF with an unlocked copy, the file's oldest revision
    is the original, so that's the one we keep.
    """

    kind = "drive"

    def __init__(
        self,
        folder_id: str,
        *,
        key_json: str | None = None,
        http: _Http | None = None,
        recursive: bool = True,
    ) -> None:
        self.folder_id = folder_id
        self.recursive = recursive
        if http is None:
            if not key_json:
                raise ConnectorError("Add the service-account key for this Drive connection.")
            http = _drive_session(key_json)
        self.http = http

    def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        try:
            r = self.http.get(url, params=params, timeout=60)
        except Exception as exc:  # noqa: BLE001 - network errors; keep the message generic
            raise ConnectorError(f"Couldn't reach Google Drive ({type(exc).__name__}).") from None
        if r.status_code in (401, 403):
            raise ConnectorError(
                "Google Drive refused access. Share the folder with the service account’s email."
            )
        if r.status_code == 404:
            raise ConnectorError("Drive says that folder or file doesn't exist (or isn't shared).")
        if r.status_code >= 400:
            raise ConnectorError(f"Google Drive answered with an error ({r.status_code}).")
        return r

    def _list(self, folder_id: str) -> Iterator[dict[str, Any]]:
        token = None
        while True:
            params = {
                "q": f"'{folder_id}' in parents and trashed = false and "
                f"(mimeType = 'application/pdf' or mimeType = '{FOLDER_MIME}')",
                "fields": "nextPageToken, files(id, name, mimeType, createdTime, headRevisionId)",
                "pageSize": 200,
                "orderBy": "createdTime",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if token:
                params["pageToken"] = token
            body = self._get(f"{DRIVE_API}/files", params).json()
            yield from body.get("files", [])
            token = body.get("nextPageToken")
            if not token:
                return

    def _files(self) -> Iterator[dict[str, Any]]:
        queue, visited = [(self.folder_id, 0)], set()
        while queue:
            folder, depth = queue.pop(0)
            if folder in visited:
                continue
            visited.add(folder)
            for f in self._list(folder):
                if f.get("mimeType") == FOLDER_MIME:
                    if self.recursive and depth < 4 and _DRIVE_ID.match(f.get("id", "")):
                        queue.append((f["id"], depth + 1))
                else:
                    yield f

    def _oldest_revision(self, file_id: str) -> str | None:
        try:
            body = self._get(
                f"{DRIVE_API}/files/{file_id}/revisions",
                {"fields": "revisions(id, modifiedTime)", "pageSize": 200},
            ).json()
        except ConnectorError:
            return None  # some shared files don't expose revisions; fall back to the file
        revs = sorted(body.get("revisions", []), key=lambda r: r.get("modifiedTime", ""))
        return revs[0]["id"] if revs else None

    def items(self, seen: Seen) -> Iterator[Incoming]:
        for f in self._files():
            fid = f.get("id", "")
            if not _DRIVE_ID.match(fid) or seen.has_prefix(fid + "@"):
                continue
            rev = self._oldest_revision(fid)
            if rev:
                url = f"{DRIVE_API}/files/{fid}/revisions/{rev}"
            else:
                rev, url = f.get("headRevisionId") or "head", f"{DRIVE_API}/files/{fid}"
            data = self._get(url, {"alt": "media", "supportsAllDrives": "true"}).content
            created = f.get("createdTime")
            yield Incoming(
                data=data,
                filename=f.get("name") or f"{fid}.pdf",
                source=self.kind,
                source_ref=f"{fid}@{rev}",
                received_at=datetime.fromisoformat(created.replace("Z", "+00:00"))
                if created
                else None,
            )


def _drive_session(key_json: str) -> _Http:
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    try:
        info = json.loads(key_json)
        creds = service_account.Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
            info, scopes=[DRIVE_SCOPE]
        )
    except Exception:  # noqa: BLE001 - never echo key material
        raise ConnectorError(
            "The saved service-account key couldn't be read. Paste it again."
        ) from None
    session: _Http = AuthorizedSession(creds)  # type: ignore[no-untyped-call, assignment]
    return session


class ImapSource:
    """A mailbox over IMAP. For Gmail, use an app password and the search runs server-side."""

    kind = "imap"

    def __init__(
        self,
        *,
        host: str,
        user: str,
        password: str,
        port: int = 993,
        folder: str = "INBOX",
        since: date | None = None,
        known_senders_only: bool = True,
        extra_senders: tuple[str, ...] | list[str] = (),
        mailbox_factory: Callable[[str, int], Any] | None = None,
    ) -> None:
        self.host, self.port, self.user, self.folder = host, port, user, folder
        self._password = password
        self.since = since
        self.known_only = known_senders_only
        self.extra = tuple(extra_senders)
        self._factory = mailbox_factory

    def __repr__(self) -> str:
        return f"ImapSource({self.user}@{self.host})"

    def _criteria(self) -> Any:
        from imap_tools import AND

        if self.host.endswith(("gmail.com", "googlemail.com")):
            q = "has:attachment filename:pdf"
            if self.since:
                q += f" after:{self.since:%Y/%m/%d}"
            return f'X-GM-RAW "{q}"'
        return AND(date_gte=self.since) if self.since else "ALL"

    def items(self, seen: Seen) -> Iterator[Incoming]:
        from imap_tools import AND, MailBox

        failure = None
        try:
            box = (self._factory or MailBox)(self.host, self.port)
            box.login(self.user, self._password, initial_folder=self.folder)
        except Exception as exc:  # noqa: BLE001 - server replies can echo input; stay generic
            failure = type(exc).__name__
        if failure:
            # Raised outside the except block so the server's reply isn't chained onto it.
            log.warning("IMAP login to %s failed: %s", self.host, failure)
            raise ConnectorError(
                f"Couldn't log in to {self.host} as {self.user}. "
                "Check the app password, and that IMAP is on."
            )
        try:
            headers = list(
                box.fetch(self._criteria(), headers_only=True, mark_seen=False, bulk=True)
            )
            for head in headers:
                mid = (head.headers.get("message-id") or ("",))[0].strip()
                mid = mid or f"uid:{self.folder}:{head.uid}"
                done = f"{mid}#done"
                if seen.has(done):
                    continue
                if self.known_only and not is_known_sender(head.from_, self.extra):
                    continue
                full = next(iter(box.fetch(AND(uid=head.uid), mark_seen=False, bulk=True)), None)
                if full is None:
                    continue
                received = full.date if full.date and full.date.year > 1970 else None
                for i, att in enumerate(full.attachments):
                    name = att.filename or f"attachment-{i + 1}.pdf"
                    if not (name.lower().endswith(".pdf") or att.content_type == "application/pdf"):
                        continue
                    yield Incoming(
                        data=att.payload,
                        filename=name,
                        source=self.kind,
                        source_ref=f"{mid}#{i}:{name}",
                        sender=full.from_ or None,
                        subject=full.subject or None,
                        received_at=received,
                    )
                seen.mark(done)
        finally:
            with contextlib.suppress(Exception):
                box.logout()


# --- building and running ---------------------------------------------------------------------


def build_source(
    conn: Connector, session: Session, box: SecretBox | None, today: date | None = None
) -> Source:
    cfg = json.loads(conn.config or "{}")
    secret = None
    if conn.kind in ("drive", "imap"):
        if box is None:
            raise ConnectorError("HISAAB_SECRET_KEY isn't set, so saved keys can't be read.")
        secret = get_secret(session, box, secret_name(conn.id))
        if not secret:
            raise ConnectorError("This connection's password or key hasn't been added yet.")
    if conn.kind == "folder":
        return FolderSource(cfg["path"], cfg.get("recursive", True))
    if conn.kind == "drive":
        return DriveSource(cfg["folder_id"], key_json=secret, recursive=cfg.get("recursive", True))
    if conn.kind == "imap":
        today = today or date.today()
        return ImapSource(
            host=cfg["host"],
            port=cfg.get("port", 993),
            user=cfg["user"],
            password=secret or "",
            folder=cfg.get("folder", "INBOX"),
            since=today - timedelta(days=31 * cfg.get("since_months", 24)),
            known_senders_only=cfg.get("known_senders_only", True),
            extra_senders=cfg.get("extra_senders", []),
        )
    raise ConnectorError("Unknown connection type.")


def run_source(session: Session, vault: Vault, box: SecretBox | None, source: Source) -> Summary:
    summary = Summary()
    ctx = Context.load(session)
    for item in source.items(Seen(session, source.kind)):
        summary.add(ingest(session, vault, box, item, ctx))
    return summary


def run_connector(
    factory: sessionmaker[Session],
    vault: Vault,
    box: SecretBox | None,
    connector_id: int,
    *,
    source: Source | None = None,
) -> Summary:
    with factory() as session:
        conn = session.get(Connector, connector_id)
        if conn is None:
            raise ConnectorError("That connection no longer exists.")
        now = datetime.now(UTC)
        conn.last_run_at = now
        session.commit()
        summary = Summary()
        try:
            src = source or build_source(conn, session, box)
            summary = run_source(session, vault, box, src)
        except ConnectorError as exc:
            session.rollback()
            conn.last_error = str(exc)[:500]
            summary.errors.append(str(exc))
        except Exception as exc:  # noqa: BLE001 - record it; the server keeps going
            session.rollback()
            log.error("Connection %s failed", connector_id, exc_info=True)
            conn.last_error = (
                f"Something went wrong ({type(exc).__name__}). The server log has details."
            )
            summary.errors.append(conn.last_error)
        else:
            conn.last_ok_at = now
            conn.last_error = None
        conn.last_summary = summary.text()[:300]
        session.commit()
        return summary


class Runner:
    """Runs connections, and other long jobs, one at a time in a background thread."""

    def __init__(
        self,
        factory: sessionmaker[Session],
        vault: Vault,
        box: SecretBox | None,
        after_sync: Callable[[], None] | None = None,
    ) -> None:
        self.factory, self.vault, self.box = factory, vault, box
        self.after_sync = after_sync  # e.g. read any new CAS that a sync brought in
        self._lock = threading.Lock()
        self.current: str | None = None
        self.started_at: datetime | None = None

    def start_job(self, label: str, fn: Callable[[], object], *, wait: bool = False) -> bool:
        """Run `fn` in the background unless something else is running."""
        if not self._lock.acquire(blocking=False):
            return False
        self.started_at = datetime.now(UTC)
        self.current = label
        thread = threading.Thread(
            target=self._job, args=(label, fn), name="hisaab-job", daemon=True
        )
        try:
            thread.start()
        except RuntimeError:
            self._lock.release()
            raise
        if wait:
            thread.join()
        return True

    def _job(self, label: str, fn: Callable[[], object]) -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001
            log.error("%s failed", label, exc_info=True)
        finally:
            self.current = None
            self.started_at = None
            self._lock.release()

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def start(self, ids: list[int], *, wait: bool = False) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        self.started_at = datetime.now(UTC)
        thread = threading.Thread(target=self._run, args=(ids,), name="hisaab-ingest", daemon=True)
        try:
            thread.start()
        except RuntimeError:
            self._lock.release()
            raise
        if wait:
            thread.join()
        return True

    def _run(self, ids: list[int]) -> None:
        try:
            for cid in ids:
                with self.factory() as s:
                    conn = s.get(Connector, cid)
                    self.current = conn.name if conn else None
                if conn is None:
                    continue
                try:
                    run_connector(self.factory, self.vault, self.box, cid)
                except Exception:  # noqa: BLE001
                    log.error("Connection %s failed", cid, exc_info=True)
            if self.after_sync:
                self.current = "Reading new statements"
                try:
                    self.after_sync()
                except Exception:  # noqa: BLE001
                    log.error("After-sync step failed", exc_info=True)
        finally:
            self.current = None
            self.started_at = None
            self._lock.release()
