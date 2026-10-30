"""Validate configuration from explicit values, the environment and local .env."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import SecretStr, StringConstraints, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MAX_SECRET_BYTES = 4_096


def read_secret_file(path: Path) -> str:
    """Read a one-line secret from a mounted file; errors never echo its content."""
    if not path.is_file():
        raise ValueError("secret file is missing or not a regular file")
    raw = path.read_bytes()
    if len(raw) > MAX_SECRET_BYTES:
        raise ValueError("secret file is too large")
    try:
        value = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise ValueError("secret file is not UTF-8") from None
    if not value or not value.isprintable():
        raise ValueError("secret file must hold one non-empty printable line")
    return value


class Settings(BaseSettings):
    """Reject invalid configuration before the HTTP service starts."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = (
        "agentic-infrastructure"
    )
    app_env: Literal["development", "test", "production"] = "development"
    # Tracebacks are disabled unless a developer explicitly opts in locally.
    app_debug: bool = False
    # Memory routes stay unavailable until a PostgreSQL URL is set explicitly.
    app_database_url: SecretStr | None = None
    # A mounted secret file can hold the URL instead of the environment.
    app_database_url_file: Path | None = None

    @model_validator(mode="after")
    def load_database_url_file(self) -> "Settings":
        """Take the database URL from its secret file when one is configured."""
        if self.app_database_url_file is None:
            return self
        if self.app_database_url is not None:
            raise ValueError("set APP_DATABASE_URL or APP_DATABASE_URL_FILE, not both")
        self.app_database_url = SecretStr(read_secret_file(self.app_database_url_file))
        return self


@lru_cache
def get_settings() -> Settings:
    """Read configuration once per process; restart after environment changes."""
    return Settings()
