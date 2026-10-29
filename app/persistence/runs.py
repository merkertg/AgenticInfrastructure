"""Record agent run metadata, its searches and cited references without text."""

import hashlib
from datetime import datetime

from sqlalchemy import Connection, insert

from app.application.agent import AgentRun
from app.domain.models import ReasonCode, ScopeRequest
from app.persistence import schema as db


def record_run(
    connection: Connection,
    request: ScopeRequest,
    run: AgentRun,
    *,
    question: str,
    occurred_at: datetime,
    credential_id: str | None,
) -> None:
    """Store the run outcome and references; question and answer text stay out."""
    connection.execute(
        insert(db.agent_runs).values(
            run_id=request.request_id,
            company_id=request.company_id,
            project_id=request.project_id,
            employee_id=request.employee_id,
            agent_id=request.agent_id,
            credential_id=credential_id,
            question_sha256=hashlib.sha256(question.encode("utf-8")).hexdigest(),
            provider=run.provider,
            allowed=run.reason is ReasonCode.ALLOWED,
            reason=run.reason.value,
            outcome=run.outcome.value,
            steps=run.steps,
            tool_calls=run.tool_calls,
            cited_items=len(run.citations),
            occurred_at=occurred_at,
        )
    )
    if run.searches:
        connection.execute(
            insert(db.agent_run_searches),
            [
                {
                    "run_id": request.request_id,
                    "position": position,
                    "context_request_id": search_id,
                }
                for position, search_id in enumerate(run.searches, start=1)
            ],
        )
    if run.citations:
        connection.execute(
            insert(db.agent_run_citations),
            [
                {
                    "run_id": request.request_id,
                    "position": position,
                    "company_id": request.company_id,
                    "document_id": item.document_id,
                    "version_id": item.version_id,
                    "fragment_ordinal": item.fragment,
                    "start_offset": item.start,
                    "end_offset": item.end,
                }
                for position, item in enumerate(run.citations, start=1)
            ],
        )
