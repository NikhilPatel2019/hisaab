"""M3: CAS holdings, prices, daily valuation, XIRR and history."""

from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from hisaab.invest import cas as cas_mod
from hisaab.invest.num import e6_str, to_e6, value_paise
from hisaab.invest.prices import parse_bhavcopy, parse_mfapi, parse_navall
from hisaab.invest.xirr import xirr
from hisaab.models import Account, BalanceSnapshot, Holding, InvestmentTxn

from . import cas_fixtures as fx
from .conftest import CSRF
from .pdfs import lock, make_pdf

TODAY = date(2025, 11, 4)


# --- pure functions ---------------------------------------------------------------------------


def test_xirr_matches_the_spreadsheet_function() -> None:
    # The worked example from the spreadsheet documentation: XIRR = 0.373362535.
    flows = [
        (date(2008, 1, 1), -10000.0),
        (date(2008, 3, 1), 2750.0),
        (date(2008, 10, 30), 4250.0),
        (date(2009, 2, 15), 3250.0),
        (date(2009, 4, 1), 2750.0),
    ]
    rate = xirr(flows)
    assert rate is not None and abs(rate - 0.373362535) < 1e-6  # well inside 0.01 pp
    # One year, 10%: (1.1)^(365/days) - 1 over a leap year.
    one = xirr([(date(2020, 1, 1), -1000.0), (date(2021, 1, 1), 1100.0)])
    assert one is not None and abs(one - (1.1 ** (365 / 366) - 1)) < 1e-9
    # A loss, and the cases with no answer.
    loss = xirr([(date(2024, 1, 1), -1000.0), (date(2024, 7, 1), 800.0)])
    assert loss is not None and loss < 0
    assert xirr([(date(2024, 1, 1), -1000.0)]) is None
    assert xirr([(date(2024, 1, 1), 1000.0), (date(2024, 2, 1), 500.0)]) is None


def test_exact_units_prices_and_values() -> None:
    assert to_e6("95.455") == 95_455_000 and to_e6("120.1234") == 120_123_400
    assert value_paise(to_e6("95.455") or 0, to_e6("120.1234") or 0) == 1_146_638  # ₹11,466.38
    assert value_paise(to_e6("95.455") or 0, to_e6("121") or 0) == 1_155_006  # 11550.055 rounds up
    assert e6_str(95_455_000) == "95.455" and e6_str(10_000_000) == "10"


def test_parse_amfi_navall_both_layouts() -> None:
    text = "\n".join(
        [
            "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date",
            " ",
            "Open Ended Schemes(Equity Scheme - Flexi Cap Fund)",
            "Demo Mutual Fund",
            "100001;INF000TEST01;-;Demo Flexi Cap Fund;Direct Plan;Growth;121.0000;03-Nov-2025",
            "100004;INF000TEST04;INF000TEST05;Demo Flexi Cap Fund;Direct Plan;IDCW;20.5;03-Nov-2025",
            "100009;-;-;Demo Closed Fund;Direct Plan;Growth;N.A.;03-Nov-2025",
        ]
    )
    navs = parse_navall(text)
    assert navs["INF000TEST01"] == (date(2025, 11, 3), to_dec("121.0000"), "100001")
    assert navs["INF000TEST05"][2] == "100004" and "-" not in navs
    old = "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date\n100002;INF000TEST02;-;Demo Liquid Fund;2076.1;03-Nov-2025"
    assert parse_navall(old)["INF000TEST02"][1] == to_dec("2076.1")


def to_dec(s: str) -> Any:
    from decimal import Decimal

    return Decimal(s)


BHAV_HEADER = "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric"


def bhavcopy(day: date, rows: list[tuple[str, str, str]]) -> str:
    lines = [BHAV_HEADER]
    for isin, series, close in rows:
        lines.append(
            f"{day},{day},CM,NSE,STK,1,{isin},DEMO,{series},,,,,DEMO,1,1,1,{close},{close},1"
        )
    return "\n".join(lines) + "\n"


def test_parse_bhavcopy_prefers_the_main_series() -> None:
    text = bhavcopy(
        date(2025, 11, 3),
        [
            ("INE000TEST01", "BL", "2400"),
            ("INE000TEST01", "EQ", "2600.10"),
            ("INF000TEST09", "EQ", "61.5"),
        ],
    )
    got = parse_bhavcopy(text)
    assert got["INE000TEST01"] == (date(2025, 11, 3), to_dec("2600.10"))
    assert got["INF000TEST09"][1] == to_dec("61.5")


def test_parse_mfapi() -> None:
    body = {
        "meta": {},
        "data": [
            {"date": "03-11-2025", "nav": "121.0"},
            {"date": "31-10-2025", "nav": "120.1234"},
            {"date": "bad", "nav": "1"},
        ],
    }
    assert parse_mfapi(body) == [
        (date(2025, 10, 31), to_dec("120.1234")),
        (date(2025, 11, 3), to_dec("121.0")),
    ]


# --- mapping casparser output -----------------------------------------------------------------


def test_rta_cas_maps_to_one_fund_account_without_raw_identifiers() -> None:
    res = cas_mod.from_casparser(fx.model("rta", fx.cams_detailed()))
    assert (res.file_type, res.cas_type, res.period_end) == ("CAMS", "DETAILED", date(2025, 10, 31))
    (acc,) = res.accounts
    assert acc.kind == "mf" and acc.key.startswith("mf:") and fx.PAN not in acc.key
    assert [h.isin for h in acc.holdings] == [
        "INF000TEST01",
        "INF000TEST02",
    ]  # the sold fund has no holding
    flexi = acc.holdings[0]
    assert (str(flexi.units), str(flexi.value), flexi.asset_class) == (
        "95.455",
        "11466.38",
        "equity",
    )
    assert flexi.folio.startswith("7890:") and "12345678" not in flexi.folio
    assert acc.holdings[1].asset_class == "debt"
    assert len(acc.txns) == 8 and {t.isin for t in acc.txns} == {
        "INF000TEST01",
        "INF000TEST02",
        "INF000TEST03",
    }


def test_depository_cas_maps_demat_folios_and_nps() -> None:
    res = cas_mod.from_casparser(fx.model("depo", fx.cdsl()))
    kinds = {a.kind: a for a in res.accounts}
    assert set(kinds) == {"demat", "mf", "nps"}
    demat = kinds["demat"]
    assert (
        demat.mask == "4321"
        and "00004321" not in demat.key
        and demat.name == "Demat · Demo Broking Limited"
    )
    assert {h.isin: h.asset_class for h in demat.holdings} == {
        "INE000TEST01": "equity",
        "INF000TEST09": "gold",
        "INE000TEST05": "debt",
    }
    assert str(demat.value) == "33070.00"
    # The same investor's funds land in the same account a CAMS CAS would use.
    rta = cas_mod.from_casparser(fx.model("rta", fx.cams_detailed()))
    assert kinds["mf"].key == rta.accounts[0].key
    nps = kinds["nps"]
    assert (
        nps.mask == "1234"
        and nps.holdings[0].isin.startswith("NPS-")
        and nps.holdings[0].asset_class == "equity"
    )


# --- the whole path: postbox → holdings → valuation -------------------------------------------


@pytest.fixture
def fake_casparser(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """casparser.read_cas_pdf, answering from a marker line in our synthetic PDFs."""
    import casparser

    state: dict[str, Any] = {"calls": [], "models": {}}

    def read(f: Any, password: str, output: str = "dict", **_: Any) -> Any:
        data = f.read() if hasattr(f, "read") else open(f, "rb").read()  # noqa: SIM115
        state["calls"].append(password)
        for marker, (kind, payload) in state["models"].items():
            if marker.encode() in data or marker in str(f):
                if state.get("password") is not None and password != state["password"]:
                    from casparser.exceptions import IncorrectPasswordError

                    raise IncorrectPasswordError(f"bad password {password}")
                m = fx.model(kind, payload)
                return m.model_dump_json(by_alias=True) if output == "json" else m
        from casparser.exceptions import CASParseError

        raise CASParseError("Could not identify the CAS issuer.")

    monkeypatch.setattr(casparser, "read_cas_pdf", read)
    return state


def cas_pdf(marker: str) -> bytes:
    return make_pdf(
        ["CONSOLIDATED ACCOUNT STATEMENT", "Central Depository Services (India) Limited", marker]
    )


def upload(c: TestClient, name: str, data: bytes) -> dict[str, Any]:
    r = c.post(
        "/api/documents",
        files=[("files", (name, io.BytesIO(data), "application/pdf"))],
        headers=CSRF,
    )
    assert r.status_code == 201, r.text
    out: dict[str, Any] = r.json()[0]
    return out


def run_import(c: TestClient, retry: bool = False) -> None:
    r = c.post(f"/api/investments/import?wait=true&retry_failed={str(retry).lower()}", headers=CSRF)
    assert r.status_code == 202, r.text


class FakeHttp:
    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes, self.calls = routes, []

    def get(self, url: str, headers: dict[str, str] | None = None, timeout: float = 0) -> Any:
        self.calls.append(url)
        for part, body in self.routes.items():
            if part in url:
                return FakeResp(200, body)
        return FakeResp(404, b"")


class FakeResp:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        if isinstance(body, dict):
            self._json, self.content, self.text = body, json.dumps(body).encode(), json.dumps(body)
        elif isinstance(body, bytes):
            self._json, self.content, self.text = None, body, body.decode("latin-1")
        else:
            self._json, self.content, self.text = None, body.encode(), body

    def json(self) -> Any:
        return self._json


def zipped(name: str, text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
    return buf.getvalue()


def test_cas_import_matches_the_statement_to_the_paisa(
    logged_in: TestClient, fake_casparser: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    c = logged_in
    monkeypatch.setattr("hisaab.invest.api.today", lambda _r: TODAY)
    fake_casparser["models"] = {
        "FIXTURE:cdsl-oct": ("depo", fx.cdsl()),
        "FIXTURE:cams": ("rta", fx.cams_detailed()),
    }
    doc = upload(c, "cdsl_oct.pdf", cas_pdf("FIXTURE:cdsl-oct"))
    assert doc["status"] == "filed"
    run_import(c)

    p = c.get("/api/investments").json()
    assert p["pending_imports"] == 0 and p["problems"] == []
    by_isin = {h["isin"]: h for h in p["holdings"]}
    # Units and values exactly as printed, on the statement date.
    assert (
        by_isin["INF000TEST01"]["units"] == "95.455"
        and by_isin["INF000TEST01"]["value_paise"] == 1_146_638
    )
    assert (
        by_isin["INE000TEST01"]["units"] == "10"
        and by_isin["INE000TEST01"]["value_paise"] == 2_500_500
    )
    assert by_isin["INF000TEST01"]["folio"] == "••7890"
    assert p["total_paise"] == 3_307_000 + 1_146_638 + 453_490
    assert p["statement_date"] == "2025-10-31"
    assert {a["name"] for a in p["accounts"]} == {
        "Demat · Demo Broking Limited",
        "Mutual funds",
        "NPS",
    }

    # Holdings accounts count towards net worth through their statement-day value.
    accounts = c.get("/api/accounts").json()
    demat = next(a for a in accounts if a["name"].startswith("Demat"))
    assert (
        demat["tracking"] == "holdings"
        and demat["mask"] == "4321"
        and demat["balance_paise"] == 3_307_000
    )
    assert (
        c.post(
            "/api/transactions",
            json={
                "account_id": demat["id"],
                "posted_on": "2025-11-01",
                "amount_paise": -100,
                "description": "x",
            },
            headers=CSRF,
        ).status_code
        == 422
    )

    # Reading it again, or a CAMS CAS for the same funds, adds nothing twice.
    run_import(c, retry=True)
    upload(c, "cams.pdf", cas_pdf("FIXTURE:cams"))
    run_import(c)
    upload(c, "cams-again.pdf", cas_pdf("FIXTURE:cams") + b"\n% another copy\n")
    run_import(c)
    with c.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        assert s.scalar(select(func.count(InvestmentTxn.id))) == 8
        mf = s.scalar(select(Account).where(Account.name == "Mutual funds"))
        assert mf is not None
        rows = s.scalars(select(Holding).where(Holding.account_id == mf.id)).all()
        assert len(rows) == 2  # one statement date, two funds held
    p = c.get("/api/investments").json()
    flexi = next(h for h in p["holdings"] if h["isin"] == "INF000TEST01")
    assert flexi["cost_paise"] == 1_000_050 and flexi["gain_paise"] == 1_146_638 - 1_000_050
    assert flexi["xirr_pct"] is not None
    assert p["xirr_pct"] is not None and p["xirr_covers_paise"] == 1_146_638 + 1_245_300


def test_newer_statements_win_and_older_ones_fill_history(
    logged_in: TestClient, fake_casparser: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    c = logged_in
    monkeypatch.setattr("hisaab.invest.api.today", lambda _r: TODAY)
    fake_casparser["models"] = {
        "FIXTURE:nov": ("depo", fx.cdsl("30-Nov-2025", gold_price="70.00")),
        "FIXTURE:oct": ("depo", fx.cdsl("31-Oct-2025")),
    }
    upload(c, "nov.pdf", cas_pdf("FIXTURE:nov"))
    run_import(c)
    upload(c, "oct.pdf", cas_pdf("FIXTURE:oct"))
    run_import(c)
    with c.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        demat = s.scalar(select(Account).where(Account.name.like("Demat%")))
        assert demat is not None
        snaps = dict(
            s.execute(
                select(BalanceSnapshot.as_of, BalanceSnapshot.balance_paise).where(
                    BalanceSnapshot.account_id == demat.id
                )
            ).all()
        )
        assert (
            snaps[date(2025, 10, 31)] == 3_307_000
            and snaps[date(2025, 11, 30)] == 3_307_000 + 97_500
        )
    monkeypatch.setattr("hisaab.invest.api.today", lambda _r: date(2025, 12, 2))
    gold = next(
        h for h in c.get("/api/investments").json()["holdings"] if h["isin"] == "INF000TEST09"
    )
    assert gold["statement_date"] == "2025-11-30" and gold["price"] == "70"


def test_parse_warnings_and_failures_are_shown_not_swallowed(
    logged_in: TestClient, fake_casparser: dict[str, Any]
) -> None:
    c = logged_in
    fake_casparser["models"] = {
        "FIXTURE:warn": (
            "depo",
            fx.cdsl(warnings=["Demo Gold ETF: value doesn't match units × price"]),
        )
    }
    upload(c, "warn.pdf", cas_pdf("FIXTURE:warn"))
    upload(c, "broken.pdf", cas_pdf("FIXTURE:unknown-format"))
    run_import(c)
    problems = {p["filename"]: p for p in c.get("/api/investments").json()["problems"]}
    assert (
        problems["warn.pdf"]["status"] == "warnings"
        and "Demo Gold ETF" in problems["warn.pdf"]["warnings"][0]
    )
    assert (
        problems["broken.pdf"]["status"] == "failed"
        and "CASParseError" in problems["broken.pdf"]["message"]
    )


def test_locked_cas_uses_the_saved_recipe_and_never_logs_it(
    logged_in: TestClient, fake_casparser: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    c = logged_in
    fake_casparser["models"] = {"FIXTURE:locked": ("depo", fx.cdsl())}
    fake_casparser["password"] = fx.PAN
    data = lock(cas_pdf("FIXTURE:locked"), fx.PAN)
    # The locked bytes don't show the marker, so match on the file instead.
    fake_casparser["models"] = {"": ("depo", fx.cdsl())}
    doc = upload(c, "locked.pdf", data)
    assert doc["status"] == "needs_password"
    run_import(c)
    assert fake_casparser["calls"] == []  # not even tried while locked
    c.put("/api/statement-passwords/facts", json={"pan": fx.PAN}, headers=CSRF)
    run_import(c)
    assert fake_casparser["calls"] == [fx.PAN]
    assert c.get("/api/investments").json()["total_paise"] > 0
    assert fx.PAN not in caplog.text

    # A wrong password is reported without echoing it.
    fake_casparser["password"] = "something-else"
    run_import(c, retry=True)  # nothing failed yet, so nothing to retry
    with c.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        from hisaab.models import CasImport, Document

        d = s.scalar(select(Document).where(Document.filename == "locked.pdf"))
        imp = s.scalar(select(CasImport).where(CasImport.document_id == d.id))  # type: ignore[union-attr]
        s.delete(imp)
        s.commit()
    run_import(c)
    (problem,) = c.get("/api/investments").json()["problems"]
    assert problem[
        "message"
    ] == "The saved password doesn't open this CAS." and fx.PAN not in json.dumps(problem)


def test_prices_revalue_holdings_daily(
    logged_in: TestClient, fake_casparser: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    c = logged_in
    monkeypatch.setattr("hisaab.invest.api.today", lambda _r: TODAY)
    fake_casparser["models"] = {"FIXTURE:cdsl-oct": ("depo", fx.cdsl())}
    upload(c, "cdsl_oct.pdf", cas_pdf("FIXTURE:cdsl-oct"))
    run_import(c)

    trade_day = date(2025, 11, 3)
    navall = "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date\n100001;INF000TEST01;-;Demo Flexi Cap Fund;Direct Plan;Growth;121.0000;03-Nov-2025\n"
    bhav = bhavcopy(trade_day, [("INE000TEST01", "EQ", "2600.10"), ("INF000TEST09", "EQ", "61.50")])
    http = FakeHttp(
        {
            "NAVAll.txt": navall,
            f"BhavCopy_NSE_CM_0_0_0_{trade_day:%Y%m%d}": zipped("bhav.csv", bhav),
        }
    )
    c.app.state.price_http = http  # type: ignore[attr-defined]
    assert c.post("/api/investments/prices?wait=true", headers=CSRF).status_code == 202
    # Today's (4 Nov) bhavcopy isn't out; the job steps back a day and finds 3 Nov.
    assert any("20251104" in u for u in http.calls) and any(
        "BSE_CM_0_0_0_20251104" in u for u in http.calls
    )

    p = c.get("/api/investments").json()
    h = {x["isin"]: x for x in p["holdings"]}
    # Value is AMFI NAV × units, to the paisa.
    assert h["INF000TEST01"]["price"] == "121" and h["INF000TEST01"]["value_paise"] == value_paise(
        95_455_000, 121_000_000
    )
    assert (
        h["INF000TEST01"]["price_source"] == "amfi"
        and h["INF000TEST01"]["price_date"] == "2025-11-03"
    )
    assert (
        h["INE000TEST01"]["value_paise"] == 2_600_100 and h["INE000TEST01"]["price_source"] == "nse"
    )
    # Bonds and NPS keep the statement's value; they're marked stale once it ages.
    assert h["INE000TEST05"]["price_source"] == "cas" and not h["INE000TEST05"]["stale"]
    assert p["prices_as_of"] == "2025-11-03" and p["last_prices"]["text"].startswith(
        "3 prices updated"
    )
    with c.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        snaps = dict(s.execute(select(BalanceSnapshot.as_of, BalanceSnapshot.source)).all())
    assert snaps[TODAY] == "valuation" and snaps[date(2025, 10, 31)] == "cas"

    # A week later with no new prices, fund and share prices are stale.
    monkeypatch.setattr("hisaab.invest.api.today", lambda _r: TODAY + timedelta(days=10))
    later = c.get("/api/investments").json()
    assert later["stale"] == 3


def test_price_failures_are_reported(logged_in: TestClient, fake_casparser: dict[str, Any]) -> None:
    c = logged_in
    fake_casparser["models"] = {"FIXTURE:cdsl-oct": ("depo", fx.cdsl())}
    upload(c, "cdsl_oct.pdf", cas_pdf("FIXTURE:cdsl-oct"))
    run_import(c)
    c.app.state.price_http = FakeHttp({})  # type: ignore[attr-defined]
    c.post("/api/investments/prices?wait=true", headers=CSRF)
    last = c.get("/api/investments").json()["last_prices"]
    assert last["text"].startswith("0 prices updated") and any("AMFI" in e for e in last["errors"])


def test_backfill_month_ends_from_fund_transactions(
    logged_in: TestClient, fake_casparser: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    c = logged_in
    monkeypatch.setattr("hisaab.invest.api.today", lambda _r: TODAY)
    fake_casparser["models"] = {"FIXTURE:cams": ("rta", fx.cams_detailed())}
    upload(c, "cams.pdf", cas_pdf("FIXTURE:cams"))
    run_import(c)

    def history(points: list[tuple[str, str]]) -> dict[str, Any]:
        return {"meta": {}, "data": [{"date": d, "nav": n} for d, n in points]}

    http = FakeHttp(
        {
            "/mf/100001": history(
                [
                    ("30-04-2025", "101"),
                    ("30-05-2025", "111"),
                    ("30-06-2025", "112"),
                    ("31-07-2025", "113"),
                    ("29-08-2025", "114"),
                    ("30-09-2025", "115"),
                    ("31-10-2025", "120.1234"),
                ]
            ),
            "/mf/100002": history(
                [
                    ("30-06-2025", "2010"),
                    ("31-07-2025", "2020"),
                    ("29-08-2025", "2030"),
                    ("30-09-2025", "2060"),
                    ("31-10-2025", "2075.5"),
                ]
            ),
            "/mf/100003": history(
                [
                    ("30-04-2025", "102"),
                    ("30-05-2025", "104"),
                    ("30-06-2025", "106"),
                    ("31-07-2025", "108"),
                    ("29-08-2025", "111"),
                ]
            ),
        }
    )
    c.app.state.price_http = http  # type: ignore[attr-defined]
    assert c.post("/api/investments/backfill?wait=true&months=12", headers=CSRF).status_code == 202
    with c.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        mf = s.scalar(select(Account).where(Account.name == "Mutual funds"))
        snaps = dict(
            s.execute(
                select(BalanceSnapshot.as_of, BalanceSnapshot.balance_paise).where(
                    BalanceSnapshot.account_id == mf.id
                )
            ).all()
        )  # type: ignore[union-attr]
    # End of June: 95.455 flexi units × 112, 10 liquid units × 2010, 10 old-fund units × 106.
    assert snaps[date(2025, 6, 30)] == value_paise(95_455_000, 112_000_000) + 2_010_000 + 106_000
    # End of September: the old fund is sold, 6 liquid units left.
    assert snaps[date(2025, 9, 30)] == value_paise(95_455_000, 115_000_000) + value_paise(
        6_000_000, 2_060_000_000
    )
    # The statement's own figure for 31 Oct stays as printed.
    assert snaps[date(2025, 10, 31)] == 1_146_638 + 1_245_300
    # The home chart now has history.
    series = c.get("/api/dashboard", params={"months": 12}).json()["series"]
    assert any(p["net_worth_paise"] > 0 for p in series[:-1])


def test_cli_cas_compat_prints_only_a_verdict(
    fake_casparser: dict[str, Any],
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hisaab.cli import main

    original, unlocked = tmp_path / "orig-FIXTURE:a.pdf", tmp_path / "unl-FIXTURE:a.pdf"
    original.write_bytes(b"%PDF FIXTURE:a")
    unlocked.write_bytes(b"%PDF FIXTURE:a")
    fake_casparser["models"] = {"FIXTURE:a": ("depo", fx.cdsl())}
    monkeypatch.setattr("sys.stdin", io.StringIO("pw\n"))
    assert main(["cas-compat", str(original), str(unlocked), "--password-stdin"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("✓ Identical") and "DEMO" not in out and "25005" not in out

    other = tmp_path / "other-FIXTURE:b.pdf"
    other.write_bytes(b"%PDF FIXTURE:b")
    fake_casparser["models"]["FIXTURE:b"] = ("depo", fx.cdsl(gold_price="99.00"))
    monkeypatch.setattr("sys.stdin", io.StringIO("pw\n"))
    assert main(["cas-compat", str(original), str(other), "--password-stdin"]) == 1
    out = capsys.readouterr().out
    assert "values differ" in out and "99" not in out and "accounts[0].equities[1].price" in out


def test_real_casparser_errors_are_friendly_and_secret_free() -> None:
    """With the real (pinned) casparser: a PDF that isn't a CAS, and a wrong password."""
    with pytest.raises(cas_mod.CasError) as err:
        cas_mod.read(make_pdf(["Just a letter", "Nothing to see"]), "")
    assert "couldn't read this CAS" in str(err.value) and err.value.__context__ is None
    locked = lock(make_pdf(["CONSOLIDATED ACCOUNT STATEMENT"]), "right-one")
    with pytest.raises(cas_mod.CasError) as err:
        cas_mod.read(locked, "wrong-one")
    assert str(
        err.value
    ) == "The saved password doesn't open this CAS." and "wrong-one" not in repr(err.value)
