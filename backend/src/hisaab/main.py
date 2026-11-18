"""Application factory."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import Scope

from hisaab import __version__
from hisaab.api import private, public
from hisaab.config import Settings, get_settings
from hisaab.db import make_engine, make_sessionmaker
from hisaab.docs.api import router as docs_router
from hisaab.docs.connectors import Runner
from hisaab.docs.vault import Vault
from hisaab.invest import jobs as invest_jobs
from hisaab.invest.api import router as invest_router
from hisaab.ledger.api import router as ledger_router
from hisaab.secretstore import SecretBox
from hisaab.security import LoginLimiter

log = logging.getLogger("hisaab")

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_HEADER = "x-hisaab"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; font-src 'self'; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
}


class GuardMiddleware(BaseHTTPMiddleware):
    """Security headers on every response, and a CSRF check on state-changing API calls.

    Browsers can't add a custom header to a cross-site form post without a CORS
    preflight, which we never grant, so requiring one blocks cross-site requests.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if (
            request.method in UNSAFE_METHODS
            and request.url.path.startswith("/api/")
            and request.headers.get(CSRF_HEADER) != "1"
        ):
            response: Response = JSONResponse(
                {"detail": "Missing X-Hisaab header."}, status_code=403
            )
        else:
            response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response


class SPAStaticFiles(StaticFiles):
    """Serve the built web app; unknown paths fall back to index.html for client routing."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or path.startswith("api"):
                raise
            return await super().get_response("index.html", scope)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="hisaab",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    engine = make_engine(settings.database_url)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    app.state.login_limiter = LoginLimiter(
        settings.login_max_attempts, settings.login_window_seconds
    )
    app.state.secret_box = SecretBox(settings.secret_key) if settings.secret_key else None
    app.state.vault = Vault(settings.vault_dir)
    app.state.price_http = None  # tests swap in a fake; None means the real internet

    def after_sync() -> None:
        # A sync may have brought in a CAS: read it, so holdings are up to date.
        on = datetime.now(ZoneInfo(settings.timezone)).date()
        invest_jobs.import_cas(app.state.sessionmaker, app.state.vault, app.state.secret_box, on)

    app.state.runner = Runner(
        app.state.sessionmaker,
        app.state.vault,
        app.state.secret_box,
        after_sync=after_sync if settings.auto_import_cas else None,
    )

    session_secret = settings.session_secret
    if not session_secret:
        session_secret = secrets.token_urlsafe(48)
        log.warning("HISAAB_SESSION_SECRET isn't set; you'll be logged out on every restart.")

    app.add_middleware(GuardMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret,
        session_cookie="hisaab_session",
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=settings.cookie_secure,
    )

    app.include_router(public)
    app.include_router(private)
    app.include_router(ledger_router)
    app.include_router(docs_router)
    app.include_router(invest_router)

    if settings.static_dir and Path(settings.static_dir, "index.html").is_file():
        app.mount("/", SPAStaticFiles(directory=settings.static_dir, html=True), name="web")

    return app
