"""casparser results for made-up statements. Every name, number and ISIN here is fictional."""

from __future__ import annotations

from typing import Any

PAN = "ABCDE1234F"
FOLIO = "12345678 / 90"


def _txn(
    d: str,
    desc: str,
    amount: str | None,
    units: str | None,
    nav: str | None,
    bal: str | None,
    type_: str,
) -> dict[str, Any]:
    return {
        "date": d,
        "description": desc,
        "amount": amount,
        "units": units,
        "nav": nav,
        "balance": bal,
        "type": type_,
        "dividend_rate": None,
    }


def cams_detailed(*, warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "statement_period": {"from": "01-Apr-2025", "to": "31-Oct-2025"},
        "file_type": "CAMS",
        "cas_type": "DETAILED",
        "investor_info": {
            "name": "DEMO INVESTOR",
            "email": "demo@example.com",
            "address": "1 DEMO ROAD",
            "mobile": "+910000000000",
        },
        "folios": [
            {
                "folio": FOLIO,
                "amc": "Demo Mutual Fund",
                "name": "DEMO INVESTOR",
                "PAN": PAN,
                "KYC": "OK",
                "PANKYC": "OK",
                "schemes": [
                    {
                        "scheme": "Demo Flexi Cap Fund - Direct Plan - Growth",
                        "advisor": "INA000000000",
                        "rta_code": "D101",
                        "rta": "CAMS",
                        "type": "EQUITY",
                        "isin": "INF000TEST01",
                        "amfi": "100001",
                        "nominees": [],
                        "open": "0.000",
                        "close": "95.455",
                        "close_calculated": "95.455",
                        "valuation": {
                            "date": "2025-10-31",
                            "nav": "120.1234",
                            "cost": "10000.50",
                            "value": "11466.38",
                        },
                        "transactions": [
                            _txn(
                                "2025-04-02",
                                "SIP Purchase - Instalment 1/12",
                                "5000.00",
                                "50.000",
                                "100.0000",
                                "50.000",
                                "PURCHASE_SIP",
                            ),
                            _txn(
                                "2025-04-02",
                                "*** Stamp Duty ***",
                                "0.25",
                                None,
                                None,
                                None,
                                "STAMP_DUTY_TAX",
                            ),
                            _txn(
                                "2025-05-02",
                                "SIP Purchase - Instalment 2/12",
                                "5000.00",
                                "45.455",
                                "110.0000",
                                "95.455",
                                "PURCHASE_SIP",
                            ),
                            _txn(
                                "2025-05-02",
                                "*** Stamp Duty ***",
                                "0.25",
                                None,
                                None,
                                None,
                                "STAMP_DUTY_TAX",
                            ),
                        ],
                    },
                    {
                        "scheme": "Demo Liquid Fund - Direct Plan - Growth",
                        "advisor": None,
                        "rta_code": "D102",
                        "rta": "CAMS",
                        "type": "DEBT",
                        "isin": "INF000TEST02",
                        "amfi": "100002",
                        "nominees": [],
                        "open": "0.000",
                        "close": "6.000",
                        "close_calculated": "6.000",
                        "valuation": {
                            "date": "2025-10-31",
                            "nav": "2075.5000",
                            "cost": "12000.00",
                            "value": "12453.00",
                        },
                        "transactions": [
                            _txn(
                                "2025-06-10",
                                "Purchase",
                                "20000.00",
                                "10.000",
                                "2000.0000",
                                "10.000",
                                "PURCHASE",
                            ),
                            _txn(
                                "2025-09-15",
                                "Redemption",
                                "-8200.00",
                                "-4.000",
                                "2050.0000",
                                "6.000",
                                "REDEMPTION",
                            ),
                        ],
                    },
                    {
                        "scheme": "Demo Old Fund - Direct Plan - Growth",
                        "advisor": None,
                        "rta_code": "D103",
                        "rta": "CAMS",
                        "type": "EQUITY",
                        "isin": "INF000TEST03",
                        "amfi": "100003",
                        "nominees": [],
                        "open": "0.000",
                        "close": "0.000",
                        "close_calculated": "0.000",
                        "valuation": {
                            "date": "2025-10-31",
                            "nav": "110.0000",
                            "cost": None,
                            "value": "0.00",
                        },
                        "transactions": [
                            _txn(
                                "2025-04-10",
                                "Purchase",
                                "1000.00",
                                "10.000",
                                "100.0000",
                                "10.000",
                                "PURCHASE",
                            ),
                            _txn(
                                "2025-08-01",
                                "Redemption",
                                "-1100.00",
                                "-10.000",
                                "110.0000",
                                "0.000",
                                "REDEMPTION",
                            ),
                        ],
                    },
                ],
            }
        ],
        "parse_warnings": warnings or [],
    }


def cdsl(
    period_to: str = "31-Oct-2025", *, gold_price: str = "60.25", warnings: list[str] | None = None
) -> dict[str, Any]:
    return {
        "statement_period": {"from": "01-" + period_to[3:], "to": period_to},
        "file_type": "CDSL",
        "investor_info": {
            "name": "DEMO INVESTOR",
            "email": "demo@example.com",
            "address": "1 DEMO ROAD",
            "mobile": "+910000000000",
        },
        "accounts": [
            {
                "name": "DEMO BROKING LIMITED",
                "type": "CDSL",
                "dp_id": "12000000",
                "client_id": "00004321",
                "folios": 0,
                "balance": "33070.00",
                "owners": [{"name": "DEMO INVESTOR", "PAN": PAN}],
                "equities": [
                    {
                        "isin": "INE000TEST01",
                        "name": "DEMO INDUSTRIES LTD",
                        "num_shares": "10",
                        "price": "2500.50",
                        "value": "25005.00",
                        "symbol": "DEMOIND",
                        "exchange": "NSE",
                    },
                    {
                        "isin": "INF000TEST09",
                        "name": "DEMO GOLD ETF",
                        "num_shares": "100",
                        "price": gold_price,
                        "value": str(100 * float(gold_price)),
                        "symbol": None,
                        "exchange": None,
                    },
                ],
                "mutual_funds": [],
                "bonds": [
                    {
                        "isin": "INE000TEST05",
                        "name": "DEMO 8% BOND 2030",
                        "num_bonds": "2",
                        "value": "2040.00",
                        "face_value": "1000",
                        "coupon_rate": "8",
                        "coupon_frequency": None,
                        "maturity_date": None,
                        "market_price": "1020.00",
                    },
                ],
            },
            {
                "name": "Mutual Fund Folios",
                "type": "Mutual Fund Folios",
                "dp_id": "",
                "client_id": "",
                "folios": 1,
                "balance": "11466.38",
                "owners": [],
                "equities": [],
                "mutual_funds": [
                    {
                        "isin": "INF000TEST01",
                        "name": "Demo Flexi Cap Fund - Direct Plan - Growth",
                        "amfi": "100001",
                        "type": "EQUITY",
                        "balance": "95.455",
                        "nav": "120.1234",
                        "value": "11466.38",
                        "avg_cost": "104.76",
                        "total_cost": "10000.50",
                        "ucc": None,
                        "folio": FOLIO,
                        "pnl": None,
                        "return": None,
                    },
                ],
                "bonds": [],
            },
        ],
        "nps": {
            "pran": "XXXXXXXX1234",
            "nps_sp": "PROT",
            "value": "4534.90",
            "schemes": [
                {
                    "scheme": "DEMO PENSION FUND SCHEME E - TIER I",
                    "fund_manager": "DEMO PFM",
                    "tier": "I",
                    "asset_class": "E",
                    "units": "100.5000",
                    "nav": "45.1234",
                    "value": "4534.90",
                },
            ],
        },
        "parse_warnings": warnings or [],
    }


def model(kind: str, data: dict[str, Any]) -> Any:
    from casparser.types import CASData, NSDLCASData

    return (CASData if kind == "rta" else NSDLCASData).model_validate(data)
