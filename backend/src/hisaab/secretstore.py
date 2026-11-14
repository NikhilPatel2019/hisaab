"""Encrypted storage for values the app needs but must never display or log."""

from __future__ import annotations

import re

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from hisaab.models import Secret

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,99}$")


class SecretStoreError(Exception):
    pass


def new_key() -> str:
    return Fernet.generate_key().decode()


class SecretBox:
    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise SecretStoreError(
                "HISAAB_SECRET_KEY isn't a valid key. Run `hisaab setup` to create one."
            ) from exc

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise SecretStoreError("This secret was saved with a different key.") from exc

    def __repr__(self) -> str:  # never leak key material through repr/logging
        return "SecretBox(<hidden>)"


def validate_name(name: str) -> str:
    if not NAME_RE.match(name):
        raise SecretStoreError(
            "Secret names use lowercase letters, digits, '.', '_' or '-' (max 100)."
        )
    return name


def put_secret(session: Session, box: SecretBox, name: str, value: str) -> None:
    validate_name(name)
    row = session.get(Secret, name)
    if row is None:
        session.add(Secret(name=name, ciphertext=box.encrypt(value)))
    else:
        row.ciphertext = box.encrypt(value)
    session.commit()


def get_secret(session: Session, box: SecretBox, name: str) -> str | None:
    row = session.get(Secret, validate_name(name))
    return None if row is None else box.decrypt(row.ciphertext)


def delete_secret(session: Session, name: str) -> bool:
    row = session.get(Secret, validate_name(name))
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def list_secrets(session: Session) -> list[Secret]:
    return list(session.scalars(select(Secret).order_by(Secret.name)))
