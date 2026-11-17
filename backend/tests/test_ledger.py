from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from hisaab.db import make_engine, make_sessionmaker
from hisaab.ledger.backup import export_all, restore_all
from hisaab.ledger.balances import balances_on, dashboard
from hisaab.ledger.demo import load_demo
from hisaab.migrate import upgrade
from hisaab.models import Account, Transaction

from .conftest import CSRF

TODAY = date(2026, 11, 17)


def test_accounts_transactions_and_balances(logged_in: TestClient) -> None:
    c = logged_in
    bank = c.post(
        "/api/accounts",
        json={"name": "Salary account", "kind": "bank", "opening_balance_paise": 500000},
        headers=CSRF,
    ).json()
    card = c.post(
        "/api/accounts", json={"name": "Rewards card", "kind": "credit_card"}, headers=CSRF
    ).json()
    assert card["is_liability"] is True and bank["balance_paise"] == 500000

    cats = {x["name"]: x["id"] for x in c.get("/api/categories").json()}
    assert {"Salary", "Rent", "Own transfer", "SIP & mutual funds"} <= set(cats)

    t = c.post(
        "/api/transactions",
        json={
            "account_id": card["id"],
            "posted_on": "2026-11-02",
            "amount_paise": -45000,
            "description": "QUICKBITE FOOD ORDER",
        },
        headers=CSRF,
    )
    assert t.status_code == 201 and t.json()["category"] is None
    txn = t.json()["id"]
    p = c.patch(
        f"/api/transactions/{txn}", json={"category_id": cats["Food delivery"]}, headers=CSRF
    )
    assert p.json()["category"] == "Food delivery" and p.json()["category_kind"] == "expense"
    assert (
        c.patch(f"/api/transactions/{txn}", json={"category_id": 999999}, headers=CSRF).status_code
        == 422
    )

    accounts = {a["name"]: a for a in c.get("/api/accounts").json()}
    assert accounts["Rewards card"]["balance_paise"] == -45000
    assert accounts["Rewards card"]["transactions"] == 1

    found = c.get("/api/transactions", params={"q": "quickbite"}).json()
    assert found["total"] == 1 and found["sum_paise"] == -45000
    assert c.get("/api/transactions", params={"uncategorised": True}).json()["total"] == 0

    # Accounts with entries need force to delete.
    assert c.delete(f"/api/accounts/{card['id']}", headers=CSRF).status_code == 409
    assert c.delete(f"/api/accounts/{card['id']}?force=true", headers=CSRF).status_code == 204
    assert c.get(f"/api/transactions/{txn}").status_code in (404, 405)


def test_balance_tracked_accounts_and_checkpoints(logged_in: TestClient) -> None:
    c = logged_in
    epf = c.post(
        "/api/accounts",
        json={"name": "EPF", "kind": "retirement", "tracking": "balance"},
        headers=CSRF,
    ).json()
    c.post(
        f"/api/accounts/{epf['id']}/snapshots",
        json={"as_of": "2026-09-30", "balance_paise": 300000_00},
        headers=CSRF,
    )
    c.post(
        f"/api/accounts/{epf['id']}/snapshots",
        json={"as_of": "2026-10-31", "balance_paise": 307800_00},
        headers=CSRF,
    )
    # Same day again replaces, it doesn't duplicate.
    c.post(
        f"/api/accounts/{epf['id']}/snapshots",
        json={"as_of": "2026-10-31", "balance_paise": 308000_00},
        headers=CSRF,
    )
    snaps = c.get(f"/api/accounts/{epf['id']}/snapshots").json()
    assert [s["balance_paise"] for s in snaps] == [308000_00, 300000_00]
    assert c.get(f"/api/accounts/{epf['id']}").json()["balance_paise"] == 308000_00
    no_txn = c.post(
        "/api/transactions",
        json={
            "account_id": epf["id"],
            "posted_on": "2026-11-01",
            "amount_paise": 1,
            "description": "x",
        },
        headers=CSRF,
    )
    assert no_txn.status_code == 422

    bank = c.post(
        "/api/accounts",
        json={"name": "Bank", "kind": "bank", "opening_balance_paise": 1000_00},
        headers=CSRF,
    ).json()
    c.post(
        "/api/transactions",
        json={
            "account_id": bank["id"],
            "posted_on": "2026-11-01",
            "amount_paise": -250_00,
            "description": "tea",
        },
        headers=CSRF,
    )
    check = c.post(
        f"/api/accounts/{bank['id']}/snapshots",
        json={"as_of": "2026-11-01", "balance_paise": 750_00},
        headers=CSRF,
    ).json()
    assert check["ledger_paise"] == 750_00


def test_new_category(logged_in: TestClient) -> None:
    name = f"Pets {uuid.uuid4().hex[:6]}"
    r = logged_in.post("/api/categories", json={"name": name, "kind": "expense"}, headers=CSRF)
    assert r.status_code == 201
    again = logged_in.post("/api/categories", json={"name": name, "kind": "expense"}, headers=CSRF)
    assert again.status_code == 409


def test_demo_dashboard_and_removal(logged_in: TestClient) -> None:
    c = logged_in
    assert c.post("/api/demo", headers=CSRF).status_code == 201
    assert c.post("/api/demo", headers=CSRF).status_code == 409  # not into a full ledger

    d = c.get("/api/dashboard").json()
    accounts = c.get("/api/accounts").json()
    assert d["has_data"] is True
    assert d["net_worth_paise"] == sum(a["balance_paise"] for a in accounts)
    assert d["series"][-1]["net_worth_paise"] == d["net_worth_paise"]
    assert len(d["series"]) == 12 and len(d["cashflow"]) == 12
    assert d["uncategorised"] == 4
    assert {g["key"] for g in d["groups"]} >= {"cash", "investments", "retirement", "cards"}
    assert all(m["income_paise"] > 0 for m in d["cashflow"])
    assert d["pace"][0]["day"] == 1

    assert c.delete("/api/demo", headers=CSRF).json() == {"accounts_removed": 8}
    assert c.get("/api/dashboard").json()["has_data"] is False


def test_export_restore_round_trip_across_databases(db_url: str, tmp_path: Path) -> None:
    """Acceptance: export from SQLite, restore into the database under test (SQLite, Postgres
    or MySQL in CI), and get identical net worth and transaction counts."""
    source_url = f"sqlite:///{tmp_path / 'source.db'}"
    upgrade(source_url)
    src = make_sessionmaker(make_engine(source_url))
    with src() as s:
        load_demo(s, TODAY)
        backup = export_all(s)
        want_net = sum(balances_on(s, TODAY).values())
        want_count = s.scalar(select(func.count()).select_from(Transaction))
        want_series = [p.net_worth_paise for p in dashboard(s, TODAY).series]

    engine = make_engine(db_url)
    dst = make_sessionmaker(engine)
    with dst() as s:
        r = restore_all(s, backup, replace=True)
        assert r["transactions"] == want_count
        assert sum(balances_on(s, TODAY).values()) == want_net
        assert s.scalar(select(func.count()).select_from(Transaction)) == want_count
        assert [p.net_worth_paise for p in dashboard(s, TODAY).series] == want_series
        names = sorted(s.scalars(select(Account.name)))
        assert names == sorted(a["name"] for a in backup["accounts"])
        # Refuses to restore over data without --replace.
        try:
            restore_all(s, backup)
        except Exception as exc:  # noqa: BLE001
            assert "already has accounts" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("restore over data should fail")
    engine.dispose()


def test_export_endpoint(logged_in: TestClient) -> None:
    logged_in.post("/api/demo", headers=CSRF)
    r = logged_in.get("/api/export")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    body = r.json()
    assert body["format"] == "hisaab-backup" and len(body["accounts"]) == 8
