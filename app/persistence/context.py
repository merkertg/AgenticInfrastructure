"""Load only authorized versions for search and record context history metadata."""

import hashlib
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, exists, insert, select
from sqlalchemy.dialects.postgresql import distinct_on

from app.application.context import ContextResult
from app.domain.models import (
    AccessRequest,
    Action,
    Agent,
    Company,
    Delegation,
    Document,
    DocumentVersion,
    Employee,
    Grant,
    Project,
    ScopeRequest,
)
from app.domain.policy import evaluate_access
from app.persistence import schema as db
from app.persistence.sources import lacks_confirmation, source_denials

MAX_SEARCH_DOCUMENTS = 200


@dataclass(frozen=True, slots=True)
class ScopeSnapshot:
    """Hold the stored scope objects that passed the project-wide check."""

    company: Company
    project: Project
    employee: Employee
    agent: Agent
    delegation: Delegation


def authorized_versions(
    connection: Connection,
    request: ScopeRequest,
    scope: ScopeSnapshot,
    occurred_at: datetime,
) -> tuple[tuple[DocumentVersion, ...], bool]:
    """Return the latest active version of each readable document, capped by ID order.

    SQL narrows candidates to documents with an active read grant and, for synced
    documents, a confirmed source, so other text is never loaded;
    evaluate_access() and source_denials() still decide each candidate.
    """
    readable = exists().where(
        db.grants.c.company_id == db.documents.c.company_id,
        db.grants.c.document_id == db.documents.c.id,
        db.grants.c.project_id == db.documents.c.project_id,
        db.grants.c.employee_id == request.employee_id,
        db.grants.c.active.is_(True),
        db.grants.c.actions.contains([Action.READ.value]),
    )
    document_rows = (
        connection.execute(
            select(db.documents)
            .where(
                db.documents.c.company_id == request.company_id,
                db.documents.c.project_id == request.project_id,
                db.documents.c.active.is_(True),
                readable,
                ~lacks_confirmation(occurred_at),
            )
            .order_by(db.documents.c.id)
            .limit(MAX_SEARCH_DOCUMENTS + 1)
        )
        .mappings()
        .all()
    )
    truncated = len(document_rows) > MAX_SEARCH_DOCUMENTS
    documents = {
        row["id"]: Document(**dict(row)) for row in document_rows[:MAX_SEARCH_DOCUMENTS]
    }
    if not documents:
        return (), truncated
    version_rows = (
        connection.execute(
            select(db.versions)
            .where(
                db.versions.c.company_id == request.company_id,
                db.versions.c.document_id.in_(documents),
                db.versions.c.active.is_(True),
            )
            .ext(distinct_on(db.versions.c.document_id))
            .order_by(db.versions.c.document_id, db.versions.c.number.desc())
        )
        .mappings()
        .all()
    )
    grant_rows = (
        connection.execute(
            select(db.grants)
            .where(
                db.grants.c.company_id == request.company_id,
                db.grants.c.employee_id == request.employee_id,
                db.grants.c.document_id.in_(documents),
            )
            .order_by(db.grants.c.id)
        )
        .mappings()
        .all()
    )
    grants: dict[str, list[Grant]] = {}
    for row in grant_rows:
        values = dict(row)
        values["actions"] = frozenset(Action(value) for value in values["actions"])
        grants.setdefault(row["document_id"], []).append(Grant(**values))
    allowed: list[DocumentVersion] = []
    for row in version_rows:
        version = DocumentVersion(**dict(row))
        decision = evaluate_access(
            AccessRequest(
                request_id=request.request_id,
                company_id=request.company_id,
                project_id=request.project_id,
                employee_id=request.employee_id,
                agent_id=request.agent_id,
                document_id=version.document_id,
                version_id=version.id,
                action=Action.READ,
            ),
            company=scope.company,
            project=scope.project,
            employee=scope.employee,
            agent=scope.agent,
            delegation=scope.delegation,
            document=documents[version.document_id],
            version=version,
            grants=tuple(grants.get(version.document_id, ())),
        )
        if decision.allowed:
            allowed.append(version)
    denied = source_denials(
        connection,
        request.company_id,
        {version.document_id for version in allowed},
        Action.READ,
        occurred_at,
    )
    return (
        tuple(version for version in allowed if version.document_id not in denied),
        truncated,
    )


def record_context(
    connection: Connection,
    result: ContextResult,
    *,
    query_terms: int,
    query: str,
    max_items: int,
    occurred_at: datetime,
    credential_id: str | None,
) -> None:
    """Store the request and returned references without query text or content."""
    request = result.request
    connection.execute(
        insert(db.context_requests).values(
            request_id=request.request_id,
            company_id=request.company_id,
            project_id=request.project_id,
            employee_id=request.employee_id,
            agent_id=request.agent_id,
            credential_id=credential_id,
            query_sha256=hashlib.sha256(query.encode("utf-8")).hexdigest(),
            query_terms=query_terms,
            max_items=max_items,
            allowed=result.reason.value == "allowed",
            reason=result.reason.value,
            result=result.outcome.value,
            returned_items=len(result.items),
            truncated=result.truncated,
            occurred_at=occurred_at,
        )
    )
    if result.items:
        connection.execute(
            insert(db.context_items),
            [
                {
                    "request_id": request.request_id,
                    "position": position,
                    "company_id": request.company_id,
                    "document_id": item.document_id,
                    "version_id": item.version_id,
                    "fragment_ordinal": item.fragment,
                    "start_offset": item.start,
                    "end_offset": item.end,
                }
                for position, item in enumerate(result.items, start=1)
            ],
        )
