"""Compose the HTTP service: liveness plus authenticated memory routes."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.api.memory import MemoryServices, router, storage_unavailable
from app.config import Settings, get_settings
from app.persistence.credentials import CredentialAuthenticator
from app.persistence.database import make_engine
from app.persistence.memory import MemoryStore


class HealthResponse(BaseModel):
    """Describe liveness only, without claiming memory or access-control readiness."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["ok"] = "ok"
    service: str


def utc_now() -> datetime:
    """Read the server clock once per memory operation."""
    return datetime.now(UTC)


def create_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> FastAPI:
    """Build an independent application, allowing explicit settings in tests."""
    resolved_settings = settings if settings is not None else get_settings()
    owned_engine: Engine | None = None
    if engine is None and resolved_settings.app_database_url is not None:
        # Building the engine validates the URL but opens no connection.
        owned_engine = make_engine(
            resolved_settings.app_database_url.get_secret_value()
        )
        engine = owned_engine

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Dispose only the connection pool this application created."""
        try:
            yield
        finally:
            if owned_engine is not None:
                owned_engine.dispose()

    application = FastAPI(
        title=resolved_settings.app_name,
        version=__version__,
        debug=resolved_settings.app_debug,
        description=(
            "Staged learning project for shared corporate memory for AI agents. "
            "Stage 04 adds authenticated memory version reads and appends."
        ),
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.memory = (
        MemoryServices(
            store=MemoryStore(engine),
            authenticator=CredentialAuthenticator(engine),
            clock=clock,
        )
        if engine is not None
        else None
    )
    application.add_exception_handler(SQLAlchemyError, storage_unavailable)
    application.include_router(router)

    @application.get("/health", response_model=HealthResponse, tags=["health"])
    def health() -> HealthResponse:
        """Confirm that this HTTP application responds without probing dependencies."""
        return HealthResponse(service=resolved_settings.app_name)

    return application


app = create_app()
