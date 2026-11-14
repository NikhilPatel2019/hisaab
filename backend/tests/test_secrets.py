from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from hisaab.models import Secret
from hisaab.secretstore import SecretBox, SecretStoreError, get_secret, new_key

from .conftest import CSRF

VALUE = "PDF-pass-7731-unique-marker"


def test_secret_round_trip_is_encrypted_and_never_returned(
    logged_in: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    r = logged_in.put("/api/secrets/bank_b.pdf", json={"value": VALUE}, headers=CSRF)
    assert r.status_code == 204

    listing = logged_in.get("/api/secrets")
    assert [s["name"] for s in listing.json()] == ["bank_b.pdf"]
    assert VALUE not in listing.text

    app = logged_in.app
    with app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        row = s.scalars(select(Secret)).one()
        assert VALUE not in row.ciphertext
        assert get_secret(s, app.state.secret_box, "bank_b.pdf") == VALUE  # type: ignore[attr-defined]

    assert VALUE not in caplog.text

    assert logged_in.delete("/api/secrets/bank_b.pdf", headers=CSRF).status_code == 204
    assert logged_in.delete("/api/secrets/bank_b.pdf", headers=CSRF).status_code == 404


def test_bad_secret_name_is_rejected(logged_in: TestClient) -> None:
    r = logged_in.put("/api/secrets/Bad Name", json={"value": "x"}, headers=CSRF)
    assert r.status_code == 422


def test_wrong_key_cannot_decrypt() -> None:
    token = SecretBox(new_key()).encrypt("hello")
    with pytest.raises(SecretStoreError):
        SecretBox(new_key()).decrypt(token)


def test_box_repr_hides_key() -> None:
    key = new_key()
    assert key not in repr(SecretBox(key))


def test_invalid_key_explains_itself() -> None:
    with pytest.raises(SecretStoreError, match="hisaab setup"):
        SecretBox("not-a-key")
