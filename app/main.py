"""Compose the HTTP service: liveness, readiness and authenticated memory routes."""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.api.answers import router as answers_router
from app.api.memory import MemoryServices, router, storage_unavailable
from app.application.agent import ModelProvider
from app.application.offline_model import OfflineProvider
from app.config import Settings, get_settings
from app.persistence.credentials import CredentialAuthenticator
from app.persistence.database import SCHEMA_HEAD, make_engine, schema_revision
from app.persistence.memory import MemoryStore

logger = logging.getLogger(__name__)


class ReadyResponse(BaseModel):
    """Report whether storage answers and carries the schema this code expects."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["ready", "not_ready"]
    database: Literal["ok", "not_configured", "unavailable"]
    schema_: Literal["current", "outdated", "unknown"] = Field(alias="schema")


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
    provider: ModelProvider | None = None,
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
            "Stage 08 adds a readiness probe for the container laboratory."
        ),
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.memory = (
        MemoryServices(
            store=MemoryStore(engine),
            authenticator=CredentialAuthenticator(engine),
            clock=clock,
            provider=provider if provider is not None else OfflineProvider(),
        )
        if engine is not None
        else None
    )
    application.add_exception_handler(SQLAlchemyError, storage_unavailable)
    application.include_router(router)
    application.include_router(answers_router)

    @application.get("/health", response_model=HealthResponse, tags=["health"])
    def health() -> HealthResponse:
        """Confirm that this HTTP application responds without probing dependencies."""
        return HealthResponse(service=resolved_settings.app_name)

    @application.get(
        "/ready",
        response_model=ReadyResponse,
        responses={503: {"model": ReadyResponse, "description": "Not ready"}},
        tags=["health"],
    )
    def ready() -> JSONResponse:
        """Probe storage and the schema revision; never echo URLs or errors."""
        if engine is None:
            return _readiness(503, "not_configured", "unknown")
        try:
            revision = schema_revision(engine)
        except SQLAlchemyError as error:
            logger.warning("readiness probe failed: %s", type(error).__name__)
            return _readiness(503, "unavailable", "unknown")
        if revision != SCHEMA_HEAD:
            return _readiness(503, "ok", "outdated")
        return _readiness(200, "ok", "current")

    return application


def _readiness(status_code: int, database: str, schema: str) -> JSONResponse:
    """Serialize a readiness result; storage-dependent replies are never cached."""
    body = ReadyResponse.model_validate(
        {
            "status": "ready" if status_code == 200 else "not_ready",
            "database": database,
            "schema": schema,
        }
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(by_alias=True),
        headers={"Cache-Control": "no-store"},
    )


app = create_app()
