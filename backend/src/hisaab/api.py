"""HTTP API. Everything under /api needs a logged-in session except health and session."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from hisaab import __version__
from hisaab.config import Settings
from hisaab.db import get_session
from hisaab.secretstore import (
    SecretBox,
    SecretStoreError,
    delete_secret,
    list_secrets,
    put_secret,
)
from hisaab.security import LoginLimiter, verify_password

DbSession = Annotated[Session, Depends(get_session)]


def settings_of(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def require_user(request: Request) -> None:
    if not request.session.get("auth"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Log in first.")


def secret_box(request: Request) -> SecretBox:
    box: SecretBox | None = request.app.state.secret_box
    if box is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "HISAAB_SECRET_KEY isn't set. Run `hisaab setup`.",
        )
    return box


def client_id(request: Request) -> str:
    return request.client.host if request.client else "unknown"


public = APIRouter(prefix="/api")
private = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class SessionState(BaseModel):
    authenticated: bool
    setup_required: bool


@public.get("/health")
def health(db: DbSession) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@public.get("/auth/session")
def session_state(request: Request) -> SessionState:
    settings = settings_of(request)
    return SessionState(
        authenticated=bool(request.session.get("auth")),
        setup_required=not settings.password_hash,
    )


@public.post("/auth/login", status_code=status.HTTP_204_NO_CONTENT)
def login(body: LoginBody, request: Request, response: Response) -> None:
    settings = settings_of(request)
    limiter: LoginLimiter = request.app.state.login_limiter
    who = client_id(request)

    wait = limiter.retry_after(who)
    if wait:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many tries. Wait {wait} seconds.",
            headers={"Retry-After": str(wait)},
        )
    if not settings.password_hash:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "No password set yet. Run `hisaab setup`."
        )
    if not verify_password(settings.password_hash, body.password):
        limiter.record_failure(who)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That password didn't match.")

    limiter.reset(who)
    request.session.clear()
    request.session["auth"] = True


@public.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request) -> None:
    request.session.clear()


class Me(BaseModel):
    version: str
    database: str
    secrets_enabled: bool


@private.get("/me")
def me(request: Request) -> Me:
    return Me(
        version=__version__,
        database=request.app.state.engine.dialect.name,
        secrets_enabled=request.app.state.secret_box is not None,
    )


class SecretInfo(BaseModel):
    name: str
    updated_at: datetime


class SecretBody(BaseModel):
    value: str = Field(min_length=1, max_length=8192)


@private.get("/secrets")
def secrets_index(db: DbSession) -> list[SecretInfo]:
    """Names only. Values never leave the server."""
    return [SecretInfo(name=s.name, updated_at=s.updated_at) for s in list_secrets(db)]


@private.put("/secrets/{name}", status_code=status.HTTP_204_NO_CONTENT)
def secrets_put(
    name: str, body: SecretBody, db: DbSession, box: Annotated[SecretBox, Depends(secret_box)]
) -> None:
    try:
        put_secret(db, box, name, body.value)
    except SecretStoreError as exc:
        raise HTTPException(422, str(exc)) from exc


@private.delete("/secrets/{name}", status_code=status.HTTP_204_NO_CONTENT)
def secrets_delete(name: str, db: DbSession) -> None:
    try:
        found = delete_secret(db, name)
    except SecretStoreError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No secret with that name.")
