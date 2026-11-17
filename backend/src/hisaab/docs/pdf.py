"""Reading PDFs: is it encrypted, can a password open it, what does page one say.

Passwords pass through these functions and nowhere else. They are never logged, never put
in exception messages, and decrypted copies only ever exist in memory.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterable
from dataclasses import dataclass

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

logging.getLogger("pypdf").setLevel(logging.ERROR)

MAX_TEXT = 20_000


class NotAPdf(Exception):
    pass


@dataclass
class PdfInfo:
    encrypted: bool
    pages: int | None
    text: str | None  # first pages' text, when readable


def _reader(data: bytes) -> PdfReader:
    if not data.lstrip()[:5].startswith(b"%PDF"):
        raise NotAPdf("This file isn't a PDF.")
    try:
        return PdfReader(io.BytesIO(data), strict=False)
    except (PdfReadError, ValueError, KeyError) as exc:
        raise NotAPdf("This PDF couldn't be read.") from exc


def _text(reader: PdfReader, pages: int = 2) -> str:
    parts = []
    for page in reader.pages[:pages]:
        try:
            parts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001, S112 - a broken page shouldn't stop classification
            continue
    return "\n".join(parts)[:MAX_TEXT]


def inspect(data: bytes) -> PdfInfo:
    reader = _reader(data)
    if reader.is_encrypted:
        # Many statements use an owner password only: they open with an empty one.
        try:
            opened = bool(reader.decrypt(""))
        except Exception:  # noqa: BLE001
            opened = False
        if opened:
            return PdfInfo(encrypted=True, pages=len(reader.pages), text=_text(reader))
        return PdfInfo(encrypted=True, pages=None, text=None)
    return PdfInfo(encrypted=False, pages=len(reader.pages), text=_text(reader))


def opens_without_password(data: bytes) -> bool:
    info = inspect(data)
    return info.text is not None


def try_password(data: bytes, password: str) -> PdfInfo | None:
    """Open with this password; None if it's wrong. Never raises with the password in it."""
    reader = _reader(data)
    if not reader.is_encrypted:
        return PdfInfo(encrypted=False, pages=len(reader.pages), text=_text(reader))
    try:
        ok = reader.decrypt(password)
    except Exception:  # noqa: BLE001 - deliberately swallow: messages could echo input
        return None
    if not ok:
        return None
    return PdfInfo(encrypted=True, pages=len(reader.pages), text=_text(reader))


def first_that_opens(
    data: bytes, candidates: Iterable[tuple[str, str]]
) -> tuple[str, PdfInfo] | None:
    """Try (id, password) pairs on one parsed copy of the file; return the id that worked."""
    reader = _reader(data)
    if not reader.is_encrypted:
        return "none", PdfInfo(encrypted=False, pages=len(reader.pages), text=_text(reader))
    for cid, password in candidates:
        try:
            ok = bool(reader.decrypt(password))
        except Exception:  # noqa: BLE001 - deliberately swallow: messages could echo input
            ok = False
        if ok:
            return cid, PdfInfo(encrypted=True, pages=len(reader.pages), text=_text(reader))
    return None


def decrypted_copy(data: bytes, password: str) -> bytes:
    """An unlocked copy, in memory only, for viewing in the browser."""
    reader = _reader(data)
    if reader.is_encrypted:
        try:
            ok = bool(reader.decrypt(password))
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            raise PermissionError("The saved password no longer opens this file.")
    writer = PdfWriter(clone_from=reader)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
