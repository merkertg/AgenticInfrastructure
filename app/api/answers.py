"""Expose a bounded agent answer that uses only the server-scoped memory tool."""

from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.memory import (
    CONTEXT_NOT_AVAILABLE,
    ERROR_RESPONSES,
    Caller,
    ErrorResponse,
    ProjectId,
    authenticated_caller,
)
from app.application.agent import AgentRun, RunOutcome, run_agent
from app.application.context import ContextResult
from app.domain.models import Action, ReasonCode, ScopeRequest
from app.domain.tool import MAX_QUESTION_CHARACTERS, check_question

router = APIRouter(prefix="/memory", tags=["memory"])
_RETURNED = frozenset({RunOutcome.ANSWERED, RunOutcome.UNANSWERED})
_DENIED = frozenset({RunOutcome.DENIED, RunOutcome.EVIDENCE_REVOKED})


class AnswerBody(BaseModel):
    """Accept a question only; scope comes from the path and the credential."""

    model_config = ConfigDict(extra="forbid", strict=True)

    question: Annotated[str, Field(min_length=1, max_length=MAX_QUESTION_CHARACTERS)]

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        """Apply the domain question rules before any model or storage work."""
        return check_question(value)


class CitationResponse(BaseModel):
    """Reference one cited passage; read its version to verify the text."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    version_id: str
    version_number: int
    source: str
    fragment: int
    start: int
    end: int


class AnswerResponse(BaseModel):
    """Return a cited answer, or say explicitly that no cited answer exists."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["answered", "unanswered"]
    answer: str | None
    citations: list[CitationResponse]
    provider: str
    tool_calls: int


def _scope(caller: Caller, project_id: str) -> ScopeRequest:
    """Build a fresh project-wide read request for the authenticated agent."""
    principal = caller.principal
    return ScopeRequest(
        request_id=str(uuid4()),
        company_id=principal.company_id,
        project_id=project_id,
        employee_id=principal.employee_id,
        agent_id=principal.agent_id,
        action=Action.READ,
    )


@router.post(
    "/projects/{project_id}/answers",
    response_model=AnswerResponse,
    responses={
        502: {"model": ErrorResponse, "description": "Agent run failed a check"},
        504: {"model": ErrorResponse, "description": "Agent run time limit"},
        **ERROR_RESPONSES,
    },
)
def answer_question(
    project_id: ProjectId,
    body: AnswerBody,
    caller: Annotated[Caller, Depends(authenticated_caller)],
    response: Response,
) -> AnswerResponse:
    """Run the model with one memory tool bound to this agent and project."""
    services = caller.services
    credential_id = caller.principal.credential_id
    request = _scope(caller, project_id)
    reason = services.store.check_scope(
        request, occurred_at=caller.occurred_at, credential_id=credential_id
    )
    if reason is not ReasonCode.ALLOWED:
        # The model is not started for a project the agent cannot read.
        run = AgentRun(
            outcome=RunOutcome.DENIED, provider=services.provider.name, reason=reason
        )
    else:

        def search(query: str, max_items: int) -> ContextResult:
            """Run one tool call as this agent in this project, with fresh time."""
            return services.store.search_context(
                _scope(caller, project_id),
                query=query,
                max_items=max_items,
                occurred_at=services.clock(),
                credential_id=credential_id,
            )

        run = run_agent(
            body.question,
            provider=services.provider,
            search=search,
            limits=services.limits,
        )
    final = services.store.finish_run(
        request,
        run,
        question=body.question,
        occurred_at=services.clock(),
        credential_id=credential_id,
    )
    if final.outcome in _DENIED:
        raise HTTPException(status_code=404, detail=CONTEXT_NOT_AVAILABLE)
    if final.outcome is RunOutcome.TIME_LIMIT:
        raise HTTPException(status_code=504, detail=final.outcome.value)
    if final.outcome not in _RETURNED:
        raise HTTPException(status_code=502, detail=final.outcome.value)
    response.headers["Cache-Control"] = "no-store"
    return AnswerResponse(
        status="answered" if final.outcome is RunOutcome.ANSWERED else "unanswered",
        answer=final.answer,
        citations=[
            CitationResponse(
                document_id=item.document_id,
                version_id=item.version_id,
                version_number=item.version_number,
                source=item.source,
                fragment=item.fragment,
                start=item.start,
                end=item.end,
            )
            for item in final.citations
        ],
        provider=final.provider,
        tool_calls=final.tool_calls,
    )
