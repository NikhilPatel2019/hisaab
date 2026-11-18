"""The long-running investment steps, shared by the web app's background runner and the CLI."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from hisaab.docs.vault import Vault
from hisaab.invest import history, prices
from hisaab.invest.importer import ImportSummary, import_pending
from hisaab.invest.valuation import revalue
from hisaab.models import AppMeta
from hisaab.secretstore import SecretBox

LAST_PRICES = "invest.last_prices"
LAST_IMPORT = "invest.last_import"
LAST_BACKFILL = "invest.last_backfill"


def _remember(session: Session, key: str, payload: dict[str, Any]) -> None:
    payload = {"at": datetime.now(UTC).isoformat(timespec="seconds"), **payload}
    row = session.get(AppMeta, key)
    if row is None:
        session.add(AppMeta(name=key, value=json.dumps(payload)))
    else:
        row.value = json.dumps(payload)
    session.commit()


def recall(session: Session, key: str) -> dict[str, Any] | None:
    row = session.get(AppMeta, key)
    if row is None:
        return None
    try:
        data: dict[str, Any] = json.loads(row.value)
    except ValueError:
        return None
    return data


def import_cas(
    factory: sessionmaker[Session],
    vault: Vault,
    box: SecretBox | None,
    today: date,
    *,
    retry_failed: bool = False,
) -> ImportSummary:
    with factory() as s:
        summary = import_pending(s, vault, box, retry_failed=retry_failed)
        if summary.imported:
            revalue(s, today)
        if summary.imported or summary.failed:
            _remember(s, LAST_IMPORT, {"text": summary.text()})
    return summary


def refresh_prices(
    factory: sessionmaker[Session], today: date, http: prices.Http | None = None
) -> prices.RefreshResult:
    with factory() as s:
        res = prices.refresh(s, http or prices.http_session(), today)
        revalue(s, today)
        _remember(
            s,
            LAST_PRICES,
            {
                "text": res.text(),
                "sources": res.sources,
                "errors": res.errors,
                "missing": res.missing[:20],
            },
        )
    return res


def backfill(
    factory: sessionmaker[Session], today: date, months: int = 24, http: prices.Http | None = None
) -> dict[str, Any]:
    with factory() as s:
        res = history.backfill(s, http or prices.http_session(), today, months)
        _remember(
            s,
            LAST_BACKFILL,
            {"text": f"{res['snapshots']} month-end values", "errors": res["errors"]},
        )
    return res
