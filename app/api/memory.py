"""Expose authenticated memory version reads, appends and context search."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.application.context import ContextOutcome
from app.application.identity import Principal
from app.application.memory import MemoryOutcome
from app.domain.models import AccessRequest, Action, DocumentVersion, ScopeRequest
from app.domain.retrieval import MAX_ITEMS, MAX_QUERY_CHARACTERS, query_terms
from app.domain.versions import derive_version_id
from app.persistence.credentials import CredentialAuthenticator
from app.persistence.memory import MemoryStore

IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
UNAUTHORIZED = "invalid or missing credentials"
NOT_AVAILABLE = "memory version not available"
CONTEXT_NOT_AVAILABLE = "memory context not available"
STORAGE_UNAVAILABLE = "memory storage unavailable"
NOT_CONFIGURED = "memory storage is not configured"

logger = logging.getLogger(__name__)
bearer = HTTPBearer(
    auto_error=False,
    bearerFormat="opaque",
    description="Agent credential issued by trusted setup.",
)
router = APIRouter(prefix="/memory", tags=["memory"])

ProjectId = Annotated[str, Path(pattern=IDENTIFIER)]
DocumentId = Annotated[str, Path(pattern=IDENTIFIER)]
VersionId = Annotated[str, Path(pattern=IDENTIFIER)]


@dataclass(frozen=True, slots=True)
class MemoryServices:
    """Group the adapters and clock one application instance uses for memory."""

    store: MemoryStore
    authenticator: CredentialAuthenticator
    clock: Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class Caller:
    """Bind an authenticated principal to the server time of its operation."""

    principal: Principal
    occurred_at: datetime
    services: MemoryServices


class ErrorResponse(BaseModel):
    """Describe a refusal without echoing identifiers, reasons or document text."""

    detail: str


class VersionResponse(BaseModel):
    """Return one authorized stored revision and its provenance."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    version_id: str
    number: int
    source: str
    content: str


class AppendVersionBody(BaseModel):
    """Accept revision data only; actor, version ID and provenance are not inputs."""

    model_config = ConfigDict(extra="forbid", strict=True)

    base_version_id: Annotated[str, Field(pattern=IDENTIFIER)]
    expected_number: Annotated[int, Field(ge=1, le=999_999)]
    content: Annotated[str, Field(min_length=1, max_length=20_000)]

    @field_validator("content")
    @classmethod
    def reject_nul(cls, value: str) -> str:
        """Reject NUL characters, which PostgreSQL text columns cannot store."""
        if "\x00" in value:
            raise ValueError("content must not contain NUL characters")
        return value


class AppendVersionResponse(BaseModel):
    """Return metadata of the stored revision; write access grants no content read."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    version_id: str
    number: int
    replayed: bool


class ContextBody(BaseModel):
    """Accept a bounded query only; scope comes from the path and the credential."""

    model_config = ConfigDict(extra="forbid", strict=True)

    query: Annotated[str, Field(min_length=1, max_length=MAX_QUERY_CHARACTERS)]
    max_items: Annotated[int, Field(ge=1, le=MAX_ITEMS)] = 5

    @field_validator("query")
    @classmethod
    def check_query(cls, value: str) -> str:
        """Apply the domain query rules so invalid queries fail before storage."""
        query_terms(value)
        return value


class ContextItemResponse(BaseModel):
    """Return one authorized passage and the references that reproduce it."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    version_id: str
    version_number: int
    source: str
    fragment: int
    start: int
    end: int
    score: float
    text: str


class ContextResponse(BaseModel):
    """Report found evidence or say explicitly that no authorized evidence exists."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["found", "no_authorized_evidence"]
    items: list[ContextItemResponse]
    truncated: bool


def _unauthorized() -> HTTPException:
    """Give every credential failure the same response and challenge."""
    return HTTPException(
        status_code=401,
        detail=UNAUTHORIZED,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _not_available() -> HTTPException:
    """Hide whether a denied target is missing, foreign or merely not granted."""
    return HTTPException(status_code=404, detail=NOT_AVAILABLE)


def authenticated_caller(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Caller:
    """Resolve the actor from the bearer token; request fields never select it."""
    if credentials is None:
        raise _unauthorized()
    services: MemoryServices | None = request.app.state.memory
    if services is None:
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED)
    occurred_at = services.clock()
    principal = services.authenticator.authenticate(
        credentials.credentials, at=occurred_at
    )
    if principal is None:
        raise _unauthorized()
    return Caller(principal=principal, occurred_at=occurred_at, services=services)


async def storage_unavailable(request: Request, exc: Exception) -> JSONResponse:
    """Fail closed without exposing database messages, parameters or content."""
    logger.error("memory storage failure: %s", type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": STORAGE_UNAVAILABLE})


ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse, "description": "Invalid or missing credentials"},
    404: {"model": ErrorResponse, "description": "Not available to this agent"},
    503: {"model": ErrorResponse, "description": "Memory storage unavailable"},
}


@router.get(
    "/projects/{project_id}/documents/{document_id}/versions/{version_id}",
    response_model=VersionResponse,
    responses=ERROR_RESPONSES,
)
def read_version(
    project_id: ProjectId,
    document_id: DocumentId,
    version_id: VersionId,
    caller: Annotated[Caller, Depends(authenticated_caller)],
    response: Response,
) -> VersionResponse:
    """Return one stored version only when current rights allow and audit commits."""
    principal = caller.principal
    result = caller.services.store.read_version(
        AccessRequest(
            request_id=str(uuid4()),
            company_id=principal.company_id,
            project_id=project_id,
            employee_id=principal.employee_id,
            agent_id=principal.agent_id,
            document_id=document_id,
            version_id=version_id,
            action=Action.READ,
        ),
        occurred_at=caller.occurred_at,
        credential_id=principal.credential_id,
    )
    if result.outcome is not MemoryOutcome.READ or result.version is None:
        raise _not_available()
    response.headers["Cache-Control"] = "no-store"
    return VersionResponse(
        document_id=result.version.document_id,
        version_id=result.version.id,
        number=result.version.number,
        source=result.version.source,
        content=result.version.content,
    )


@router.post(
    "/projects/{project_id}/documents/{document_id}/versions",
    status_code=201,
    response_model=AppendVersionResponse,
    responses={
        200: {"model": AppendVersionResponse, "description": "Identical retry"},
        409: {"model": ErrorResponse, "description": "Version or key conflict"},
        **ERROR_RESPONSES,
    },
)
def append_version(
    project_id: ProjectId,
    document_id: DocumentId,
    body: AppendVersionBody,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", pattern=IDENTIFIER)
    ],
    caller: Annotated[Caller, Depends(authenticated_caller)],
    response: Response,
) -> AppendVersionResponse:
    """Append the next version for an authorized writer and replay identical retries."""
    principal = caller.principal
    number = body.expected_number + 1
    result = caller.services.store.write_version(
        AccessRequest(
            request_id=idempotency_key,
            company_id=principal.company_id,
            project_id=project_id,
            employee_id=principal.employee_id,
            agent_id=principal.agent_id,
            document_id=document_id,
            version_id=body.base_version_id,
            action=Action.WRITE,
        ),
        new_version=DocumentVersion(
            id=derive_version_id(principal.company_id, document_id, number),
            company_id=principal.company_id,
            document_id=document_id,
            number=number,
            source=f"agent:{principal.agent_id}",
            content=body.content,
        ),
        expected_number=body.expected_number,
        occurred_at=caller.occurred_at,
        credential_id=principal.credential_id,
    )
    if result.outcome is MemoryOutcome.DENIED:
        raise _not_available()
    if result.outcome is not MemoryOutcome.WRITTEN:
        raise HTTPException(status_code=409, detail=result.outcome.value)
    assert result.version_id is not None and result.version_number is not None
    if result.replayed:
        response.status_code = 200
    response.headers["Cache-Control"] = "no-store"
    return AppendVersionResponse(
        document_id=document_id,
        version_id=result.version_id,
        number=result.version_number,
        replayed=result.replayed,
    )


@router.post(
    "/projects/{project_id}/context",
    response_model=ContextResponse,
    responses=ERROR_RESPONSES,
)
def search_context(
    project_id: ProjectId,
    body: ContextBody,
    caller: Annotated[Caller, Depends(authenticated_caller)],
    response: Response,
) -> ContextResponse:
    """Return ranked passages from documents this agent may read right now."""
    principal = caller.principal
    result = caller.services.store.search_context(
        ScopeRequest(
            request_id=str(uuid4()),
            company_id=principal.company_id,
            project_id=project_id,
            employee_id=principal.employee_id,
            agent_id=principal.agent_id,
            action=Action.READ,
        ),
        query=body.query,
        max_items=body.max_items,
        occurred_at=caller.occurred_at,
        credential_id=principal.credential_id,
    )
    if result.outcome is ContextOutcome.DENIED:
        raise HTTPException(status_code=404, detail=CONTEXT_NOT_AVAILABLE)
    response.headers["Cache-Control"] = "no-store"
    return ContextResponse(
        status="found"
        if result.outcome is ContextOutcome.FOUND
        else "no_authorized_evidence",
        items=[
            ContextItemResponse(
                document_id=item.document_id,
                version_id=item.version_id,
                version_number=item.version_number,
                source=item.source,
                fragment=item.fragment,
                start=item.start,
                end=item.end,
                score=item.score,
                text=item.text,
            )
            for item in result.items
        ],
        truncated=result.truncated,
    )
