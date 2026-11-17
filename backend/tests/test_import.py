"""CSV import. Every file here is invented; real statements never go in the repo."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from .conftest import CSRF

# Bank style 1: preamble, newest first, separate withdrawal/deposit columns, footer,
# and two identical ₹20 teas on the same day.
BANK_SPLIT = """Demo Bank Ltd.,,,,,,
Statement of account,,,,,,
Account No : XXXXXXXX2041,,,,,,
,,,,,,
Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance
05/10/2026,UPI-CORNER CHAI-PAYTM,,05/10/2026,20.00,,"1,10,709.50"
05/10/2026,UPI-CORNER CHAI-PAYTM,,05/10/2026,20.00,,"1,10,729.50"
05/10/2026,UPI-GREENCART GROCERS-YESB,REF003,05/10/2026,"1,250.50",,"1,10,749.50"
03/10/2026,UPI-R MEHTA-LANDLORD-RENT,REF002,03/10/2026,"18,000.00",,"1,12,000.00"
02/10/2026,NEFT CR-ACME SOFTWARE-SALARY,REF001,02/10/2026,,"1,20,000.00","1,30,000.00"
,,,,,,
,,,Total,"19,290.50","1,20,000.00",
*** End of statement ***,,,,,,
"""

# A later export that overlaps by one day and adds one new row.
BANK_SPLIT_OVERLAP = """Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance
05/10/2026,UPI-GREENCART GROCERS-YESB,REF003,05/10/2026,"1,250.50",,"1,10,749.50"
05/10/2026,UPI-CORNER CHAI-PAYTM,,05/10/2026,20.00,,"1,10,729.50"
05/10/2026,UPI-CORNER CHAI-PAYTM,,05/10/2026,20.00,,"1,10,709.50"
07/10/2026,UPI-CITY METRO-RECHARGE,REF004,07/10/2026,200.00,,"1,10,509.50"
"""

# Bank style 2: amount plus a Dr/Cr column, dashes in dates.
BANK_TYPE = """Sl. No.,Transaction Date,Value Date,Description,Chq / Ref No.,Amount,Dr / Cr,Balance,Dr / Cr
1,01-11-2026,01-11-2026,UPI/GOKUL DAIRY/YESB,UPI-1,58.00,DR,"9,942.00",CR
2,02-11-2026,02-11-2026,IMPS/REFUND/SHOPKART,IMPS-2,499.00,CR,"10,441.00",CR
"""

# A card export: spends are positive, a payment carries an explicit "Cr".
CARD = """Date,Transaction Details,Amount (in Rs.)
12 Oct 2026,QUICKBITE FOOD ORDER,450.00
13 Oct 2026,STREAMBOX SUBSCRIPTION,199.00
15 Oct 2026,PAYMENT RECEIVED - THANK YOU,"1,000.00 Cr"
"""


def _account(c: TestClient, kind: str = "bank", name: str = "Salary account") -> int:
    r = c.post("/api/accounts", json={"name": name, "kind": kind}, headers=CSRF)
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


def _import(c: TestClient, account_id: int, content: str, name: str = "x.csv") -> dict[str, Any]:
    body = {"account_id": account_id, "filename": name, "content": content}
    p = c.post("/api/imports/preview", json=body, headers=CSRF)
    assert p.status_code == 200, p.text
    r = c.post("/api/imports", json={**body, "mapping": p.json()["mapping"]}, headers=CSRF)
    assert r.status_code == 201, r.text
    return {"preview": p.json(), **r.json()}


def test_split_columns_preamble_and_footer(logged_in: TestClient) -> None:
    acct = _account(logged_in)
    p = logged_in.post(
        "/api/imports/preview",
        json={"account_id": acct, "filename": "oct.csv", "content": BANK_SPLIT},
        headers=CSRF,
    ).json()
    m = p["mapping"]
    assert (m["header_row"], m["date_col"], m["amount_mode"]) == (3, 0, "split")
    assert (m["debit_col"], m["credit_col"], m["balance_col"], m["reference_col"]) == (4, 5, 6, 2)
    assert p["rows_found"] == 5 and p["rows_new"] == 5 and p["rows_skipped"] >= 1
    assert p["first_date"] == "2026-10-02" and p["last_date"] == "2026-10-05"


def test_import_twice_adds_nothing_and_balance_adds_up(logged_in: TestClient) -> None:
    acct = _account(logged_in)
    first = _import(logged_in, acct, BANK_SPLIT, "oct.csv")
    assert first["rows_added"] == 5
    # Opening balance worked out from the first row's running balance.
    assert first["opening_balance_set_paise"] == 10_000_00
    assert first["balance_check"] == {
        "on": "2026-10-05",
        "file_paise": 1_10_709_50,
        "ledger_paise": 1_10_709_50,
        "matches": True,
    }

    again = _import(logged_in, acct, BANK_SPLIT, "oct.csv")
    assert again["rows_added"] == 0 and again["rows_duplicate"] == 5
    assert again["same_file_as_import"] == first["import_id"]
    assert again["preview"]["rows_new"] == 0

    overlap = _import(logged_in, acct, BANK_SPLIT_OVERLAP, "oct-nov.csv")
    assert overlap["rows_added"] == 1 and overlap["rows_duplicate"] == 3
    assert overlap["balance_check"]["matches"] is True

    page = logged_in.get("/api/transactions", params={"account_id": acct}).json()
    assert page["total"] == 6
    assert logged_in.get(f"/api/accounts/{acct}").json()["balance_paise"] == 1_10_509_50
    teas = [t for t in page["items"] if "CHAI" in t["description"]]
    assert len(teas) == 2 and teas[0]["payee"] == "Corner Chai"


def test_type_column_and_undo(logged_in: TestClient) -> None:
    acct = _account(logged_in, name="Spending account")
    r = _import(logged_in, acct, BANK_TYPE)
    assert r["preview"]["mapping"]["amount_mode"] == "type"
    assert r["preview"]["mapping"]["date_format"] == "%d-%m-%Y"
    items = logged_in.get("/api/transactions", params={"account_id": acct}).json()["items"]
    assert sorted(t["amount_paise"] for t in items) == [-5800, 49900]
    assert r["balance_check"]["matches"] is True

    imports = logged_in.get("/api/imports").json()
    assert imports[0]["transactions_left"] == 2
    undo = logged_in.delete(f"/api/imports/{r['import_id']}", headers=CSRF)
    assert undo.json() == {"transactions_removed": 2}
    assert logged_in.get("/api/transactions", params={"account_id": acct}).json()["total"] == 0


def test_card_spends_flip_but_explicit_cr_does_not(logged_in: TestClient) -> None:
    acct = _account(logged_in, kind="credit_card", name="Food card")
    r = _import(logged_in, acct, CARD)
    assert r["preview"]["mapping"]["invert"] is True
    items = logged_in.get("/api/transactions", params={"account_id": acct}).json()["items"]
    by_desc = {t["description"]: t["amount_paise"] for t in items}
    assert by_desc["QUICKBITE FOOD ORDER"] == -45000
    assert by_desc["PAYMENT RECEIVED - THANK YOU"] == 100000
    assert logged_in.get(f"/api/accounts/{acct}").json()["balance_paise"] == 100000 - 45000 - 19900


def test_category_column_is_matched_by_name(logged_in: TestClient) -> None:
    acct = _account(logged_in)
    csv = (
        "Date,Description,Amount,Category\n01/11/2026,Rent to landlord,-18000,rent\n"
        "02/11/2026,Mystery shop,-50,Not a category\n"
    )
    r = _import(logged_in, acct, csv)
    assert r["uncategorised"] == 1
    items = logged_in.get("/api/transactions", params={"account_id": acct}).json()["items"]
    assert {t["description"]: t["category"] for t in items}["Rent to landlord"] == "Rent"


def test_nonsense_file_is_explained(logged_in: TestClient) -> None:
    acct = _account(logged_in)
    r = logged_in.post(
        "/api/imports/preview",
        json={"account_id": acct, "filename": "x.csv", "content": "hello,world\n1,2\n"},
        headers=CSRF,
    )
    assert r.status_code == 422
    assert "header" in r.json()["detail"]


def test_balance_accounts_cannot_import(logged_in: TestClient) -> None:
    r = logged_in.post(
        "/api/accounts",
        json={"name": "EPF", "kind": "retirement", "tracking": "balance"},
        headers=CSRF,
    )
    body = {"account_id": r.json()["id"], "filename": "x.csv", "content": CARD}
    assert logged_in.post("/api/imports/preview", json=body, headers=CSRF).status_code == 422


def test_bad_mapping_from_a_client_is_a_clear_error(logged_in: TestClient) -> None:
    acct = _account(logged_in)
    body = {"account_id": acct, "filename": "x.csv", "content": BANK_TYPE}
    m = logged_in.post("/api/imports/preview", json=body, headers=CSRF).json()["mapping"]
    for bad in ({"header_row": 99}, {"amount_col": 42}, {"amount_mode": "split"}):
        r = logged_in.post(
            "/api/imports/preview", json={**body, "mapping": {**m, **bad}}, headers=CSRF
        )
        assert r.status_code == 422, bad
        r = logged_in.post("/api/imports", json={**body, "mapping": {**m, **bad}}, headers=CSRF)
        assert r.status_code == 422, bad
