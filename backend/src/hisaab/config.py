"""Settings, read from environment variables or a `.env` file.

Every variable is prefixed with ``HISAAB_``. The database URL also accepts the
conventional ``DATABASE_URL`` so hosted platforms work without renaming.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
DEFAULT_DATABASE_URL = "sqlite:///./data/hisaab.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HISAAB_",
        env_file=os.environ.get("HISAAB_ENV_FILE", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    database_url: str = Field(
        default=DEFAULT_DATABASE_URL,
        validation_alias=AliasChoices("HISAAB_DATABASE_URL", "DATABASE_URL"),
    )
    host: str = "127.0.0.1"
    port: int = 8000

    # Argon2 hash of the login password. Created by `hisaab setup`.
    password_hash: str | None = None
    # Signs the session cookie. Created by `hisaab setup`.
    session_secret: str | None = None
    # Fernet key that encrypts stored secrets (bank PDF passwords, API keys).
    secret_key: str | None = None

    cookie_secure: bool = False
    session_max_age: int = 60 * 60 * 24 * 7
    login_max_attempts: int = 5
    login_window_seconds: int = 300
    forwarded_allow_ips: str = "127.0.0.1"

    # Where original statements are kept, named by their SHA-256. Back this folder up.
    vault_dir: str = "./data/vault"

    # Read a CAS into holdings as soon as it lands in the postbox.
    auto_import_cas: bool = True

    # Used for "today" (month boundaries, the dashboard).
    timezone: str = "Asia/Kolkata"

    # Built frontend. When set and present, the API also serves the web app.
    static_dir: str | None = None

    @property
    def is_public_bind(self) -> bool:
        return self.host not in LOCAL_HOSTS


@lru_cache
def get_settings() -> Settings:
    return Settings()
