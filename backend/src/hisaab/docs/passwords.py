"""Statement passwords.

Indian banks, card issuers and depositories build PDF passwords from a few personal facts:
the first letters of your name, your date of birth, PAN, mobile number, or the last digits of
a card. You save those facts once (encrypted), plus any one-off passwords, and hisaab tries
the likely combinations. When one works, it remembers *which recipe* (never the password) so
next month's statement opens first time.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from hisaab.models import Account, AppMeta, Secret
from hisaab.secretstore import SecretBox, delete_secret, get_secret, put_secret

FACTS = ("name", "dob", "pan", "mobile")
PW_PREFIX = "pdf.pw."
FACT_PREFIX = "fact."
_PAN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


class FactError(ValueError):
    pass


def normalise_fact(key: str, value: str) -> str:
    v = value.strip()
    if key == "pan":
        v = v.upper().replace(" ", "")
        if not _PAN.match(v):
            raise FactError("PAN looks like ABCDE1234F.")
    elif key == "dob":
        try:
            date.fromisoformat(v)
        except ValueError as exc:
            raise FactError("Date of birth should be YYYY-MM-DD.") from exc
    elif key == "mobile":
        v = re.sub(r"\D", "", v)[-10:]
        if len(v) != 10:
            raise FactError("Mobile should have 10 digits.")
    elif key == "name":
        if not re.search(r"[A-Za-z]", v):
            raise FactError("Name should have letters.")
    else:
        raise FactError("Unknown fact.")
    return v


def set_fact(session: Session, box: SecretBox, key: str, value: str | None) -> None:
    if key not in FACTS:
        raise FactError("Unknown fact.")
    name = FACT_PREFIX + key
    if not value:
        delete_secret(session, name)
        return
    put_secret(session, box, name, normalise_fact(key, value))


def get_facts(session: Session, box: SecretBox) -> dict[str, str]:
    out = {}
    for key in FACTS:
        v = get_secret(session, box, FACT_PREFIX + key)
        if v:
            out[key] = v
    return out


def facts_present(session: Session) -> dict[str, bool]:
    names = set(session.scalars(select(Secret.name).where(Secret.name.like(FACT_PREFIX + "%"))))
    return {k: FACT_PREFIX + k in names for k in FACTS}


def saved_passwords(session: Session) -> list[Secret]:
    return list(
        session.scalars(
            select(Secret).where(Secret.name.like(PW_PREFIX + "%")).order_by(Secret.created_at)
        )
    )


def add_password(session: Session, box: SecretBox, label: str, password: str) -> str:
    """Save a password under a label; if the same password is already saved, reuse it."""
    saved = saved_passwords(session)
    for s in saved:
        if box.decrypt(s.ciphertext) == password:
            return s.name
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40] or "password"
    existing = {s.name for s in saved}
    name, n = f"{PW_PREFIX}{slug}", 2
    while name in existing:
        name, n = f"{PW_PREFIX}{slug}-{n}", n + 1
    put_secret(session, box, name, password)
    return name


# --- recipes ---------------------------------------------------------------------------------


def _name4(name: str) -> str:
    first = re.sub(r"[^A-Za-z]", "", name.split()[0]) if name.split() else ""
    return first[:4]


def recipes(facts: dict[str, str], masks: list[str]) -> Iterator[tuple[str, str]]:
    """(recipe id, password) pairs. Ids describe the recipe and never contain the password."""
    name4 = _name4(facts.get("name", ""))
    dob = date.fromisoformat(facts["dob"]) if "dob" in facts else None
    pan = facts.get("pan")
    mobile = facts.get("mobile")
    d = (
        {
            "ddmm": dob.strftime("%d%m"),
            "ddmmyyyy": dob.strftime("%d%m%Y"),
            "ddmmyy": dob.strftime("%d%m%y"),
            "yyyymmdd": dob.strftime("%Y%m%d"),
            "yyyy": dob.strftime("%Y"),
        }
        if dob
        else {}
    )
    names = (
        {"upper": name4.upper(), "lower": name4.lower(), "title": name4.title()} if name4 else {}
    )

    if pan:
        yield "pan", pan.upper()
        yield "pan_lower", pan.lower()
    for k in ("ddmmyyyy", "ddmmyy", "yyyymmdd", "ddmm"):
        if k in d:
            yield f"dob_{k}", d[k]
    for case, n in names.items():
        for k in ("ddmm", "ddmmyyyy", "yyyy"):
            if k in d:
                yield f"name4_{case}+dob_{k}", n + d[k]
    if pan and "ddmmyyyy" in d:
        yield "pan+dob_ddmmyyyy", pan.upper() + d["ddmmyyyy"]
    if mobile:
        if "ddmmyy" in d:
            yield "mobile_last5+dob_ddmmyy", mobile[-5:] + d["ddmmyy"]
        if "ddmm" in d:
            yield "mobile_last4+dob_ddmm", mobile[-4:] + d["ddmm"]
        for case, n in names.items():
            yield f"name4_{case}+mobile_last4", n + mobile[-4:]
    for mask in masks:
        if "ddmmyyyy" in d:
            yield f"dob_ddmmyyyy+mask:{mask}", d["ddmmyyyy"] + mask
        if "ddmm" in d:
            yield f"mask:{mask}+dob_ddmm", mask + d["ddmm"]
        for case in ("upper", "lower"):
            if case in names:
                yield f"name4_{case}+mask:{mask}", names[case] + mask


def _masks(session: Session) -> list[str]:
    return sorted(
        {m for m in session.scalars(select(Account.mask)) if m and m.isdigit() and len(m) == 4}
    )


def candidates(session: Session, box: SecretBox, first: list[str]) -> Iterator[tuple[str, str]]:
    """Every password worth trying, hinted ones first, each password once."""
    seen: set[str] = set()
    pool: dict[str, str] = {}
    for s in saved_passwords(session):
        pool["secret:" + s.name] = box.decrypt(s.ciphertext)
    for rid, pw in recipes(get_facts(session, box), _masks(session)):
        pool.setdefault("recipe:" + rid, pw)
    order = [c for c in first if c in pool] + [c for c in pool if c not in first]
    for cid in order:
        pw = pool[cid]
        if pw and pw not in seen:
            seen.add(pw)
            yield cid, pw


def resolve(session: Session, box: SecretBox, candidate_id: str) -> str | None:
    if candidate_id == "empty":
        return ""
    if candidate_id.startswith("secret:"):
        return get_secret(session, box, candidate_id.removeprefix("secret:"))
    if candidate_id.startswith("recipe:"):
        rid = candidate_id.removeprefix("recipe:")
        for r, pw in recipes(get_facts(session, box), _masks(session)):
            if r == rid:
                return pw
    return None


def _hint_key(key: str) -> str:
    return "unlock." + hashlib.sha1(key.lower().encode(), usedforsecurity=False).hexdigest()[:20]


HINTS_KEPT = 3


def hints(session: Session, keys: list[str]) -> list[str]:
    out: list[str] = []
    for k in keys:
        row = session.get(AppMeta, _hint_key(k))
        for cid in row.value.split("|") if row else []:
            if cid and cid not in out:
                out.append(cid)
    return out


def remember(session: Session, keys: list[str], candidate_id: str) -> None:
    """Note which password worked for this sender, most recent first. Never the password."""
    for k in keys:
        name = _hint_key(k)
        row = session.get(AppMeta, name)
        prior = [c for c in (row.value.split("|") if row else []) if c and c != candidate_id]
        value = "|".join([candidate_id, *prior][:HINTS_KEPT])
        if row is None:
            session.add(AppMeta(name=name, value=value))
        else:
            row.value = value


_FACT_WORDS = {
    "name4": "name",
    "dob": "date of birth",
    "pan": "PAN",
    "mobile": "mobile",
    "mask": "card digits",
}


def describe(candidate_id: str | None) -> str | None:
    """A human label for how a file was opened, e.g. "name + date of birth"."""
    if not candidate_id:
        return None
    if candidate_id in ("empty", "none"):
        return "no password needed"
    if candidate_id.startswith("secret:"):
        return "saved password “" + candidate_id.removeprefix("secret:" + PW_PREFIX) + "”"
    if candidate_id.startswith("recipe:"):
        parts = []
        for piece in candidate_id.removeprefix("recipe:").split("+"):
            head = re.split(r"[_:]", piece, maxsplit=1)[0]
            word = _FACT_WORDS.get(head, head)
            if word not in parts:
                parts.append(word)
        return " + ".join(parts)
    return None
