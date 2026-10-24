"""Validate configuration from explicit values, the environment and local .env."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import SecretStr, StringConstraints
from pydantic_settings import BaseSettings, SettingsConfigDict


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


@lru_cache
def get_settings() -> Settings:
    """Read configuration once per process; restart after environment changes."""
    return Settings()
