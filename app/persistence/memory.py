"""Commit PostgreSQL memory operations, policy checks and audit as one transaction."""

import hashlib
import json
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Connection, Engine, Table, func, insert, select
from sqlalchemy.engine import RowMapping

from app.application.agent import AgentRun, RunOutcome
from app.application.context import ContextItem, ContextOutcome, ContextResult
from app.application.memory import MemoryOutcome, MemoryResult
from app.domain.audit import make_audit_event
from app.domain.models import (
    AccessDecision,
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
    ReasonCode,
    ScopeRequest,
)
from app.domain.policy import evaluate_access, evaluate_scope
from app.domain.retrieval import (
    Candidate,
    RankedFragment,
    query_terms,
    rank,
    select_context,
)
from app.domain.text import split_fragments
from app.domain.tool import check_question
from app.persistence import schema as db
from app.persistence.context import ScopeSnapshot, authorized_versions, record_context
from app.persistence.credentials import credential_is_current
from app.persistence.runs import record_run
from app.persistence.sources import source_denials


def _row(
    connection: Connection, table: Table, company_id: str, identifier: str
) -> RowMapping | None:
    """Read one object using both its company and local identifier."""
    return (
        connection.execute(
            select(table).where(
                table.c.company_id == company_id, table.c.id == identifier
            )
        )
        .mappings()
        .one_or_none()
    )


def _actions(row: RowMapping) -> dict[str, Any]:
    """Reconstruct validated immutable action sets from database JSON values."""
    values = dict(row)
    values["actions"] = frozenset(Action(value) for value in values["actions"])
    return values


def _check_call(
    request: AccessRequest,
    action: Action,
    occurred_at: datetime,
    credential_id: str | None,
) -> None:
    """Reject malformed operation calls before opening a database transaction."""
    if not isinstance(request, AccessRequest):
        raise TypeError("request must be an AccessRequest")
    if request.action is not action:
        raise ValueError("request action does not match the operation")
    if not isinstance(occurred_at, datetime):
        raise TypeError("occurred_at must be a datetime")
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise ValueError("occurred_at must be timezone-aware")
    if credential_id is not None and not isinstance(credential_id, str):
        raise TypeError("credential_id must be a string or None")


def _check_scope_call(
    request: ScopeRequest, occurred_at: datetime, credential_id: str | None
) -> None:
    """Reject malformed project-wide calls before opening a database transaction."""
    if not isinstance(request, ScopeRequest):
        raise TypeError("request must be a ScopeRequest")
    if request.action is not Action.READ:
        raise ValueError("project-wide operations require the read action")
    if not isinstance(occurred_at, datetime):
        raise TypeError("occurred_at must be a datetime")
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise ValueError("occurred_at must be timezone-aware")
    if credential_id is not None and not isinstance(credential_id, str):
        raise TypeError("credential_id must be a string or None")


class MemoryStore:
    """Serve trusted callers; actor IDs are verified only via a rechecked credential."""

    def __init__(self, engine: Engine) -> None:
        """Use an explicit engine without connecting or importing web settings."""
        if engine.dialect.name != "postgresql":
            raise ValueError("MemoryStore requires a PostgreSQL engine")
        self.engine = engine

    @contextmanager
    def _transaction(self) -> Iterator[Connection]:
        """Set isolation on each checked-out connection before its transaction."""
        with self.engine.connect() as connection:
            # Override inherited engine options after checkout listeners have run.
            connection = connection.execution_options(isolation_level="READ COMMITTED")
            with connection.begin():
                yield connection

    def _decision(
        self,
        connection: Connection,
        request: AccessRequest,
        credential_id: str | None,
        occurred_at: datetime,
    ) -> tuple[AccessDecision, DocumentVersion | None]:
        """Lock the company, then evaluate current records and the source gate."""
        company_row = (
            connection.execute(
                select(db.companies)
                .where(db.companies.c.id == request.company_id)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        # A revocation committed after HTTP authentication is visible under the lock.
        if credential_id is not None and not credential_is_current(
            connection, request, credential_id, occurred_at
        ):
            return (
                AccessDecision(
                    request=request,
                    allowed=False,
                    reason=ReasonCode.CREDENTIAL_UNAVAILABLE,
                ),
                None,
            )
        project_row = _row(
            connection, db.projects, request.company_id, request.project_id
        )
        employee_row = _row(
            connection, db.employees, request.company_id, request.employee_id
        )
        agent_row = _row(connection, db.agents, request.company_id, request.agent_id)
        document_row = _row(
            connection, db.documents, request.company_id, request.document_id
        )
        version_row = _row(
            connection, db.versions, request.company_id, request.version_id
        )
        delegation_row = (
            connection.execute(
                select(db.delegations).where(
                    db.delegations.c.company_id == request.company_id,
                    db.delegations.c.project_id == request.project_id,
                    db.delegations.c.employee_id == request.employee_id,
                    db.delegations.c.agent_id == request.agent_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        grant_rows = (
            connection.execute(
                select(db.grants)
                .where(
                    db.grants.c.company_id == request.company_id,
                    db.grants.c.employee_id == request.employee_id,
                    db.grants.c.document_id == request.document_id,
                )
                .order_by(db.grants.c.id)
            )
            .mappings()
            .all()
        )
        version = (
            DocumentVersion(**dict(version_row)) if version_row is not None else None
        )
        decision = evaluate_access(
            request,
            company=Company(**dict(company_row)) if company_row is not None else None,
            project=Project(**dict(project_row)) if project_row is not None else None,
            employee=Employee(**dict(employee_row))
            if employee_row is not None
            else None,
            agent=Agent(**dict(agent_row)) if agent_row is not None else None,
            delegation=Delegation(**_actions(delegation_row))
            if delegation_row is not None
            else None,
            document=Document(**dict(document_row))
            if document_row is not None
            else None,
            version=version,
            grants=tuple(Grant(**_actions(row)) for row in grant_rows),
        )
        if decision.allowed:
            denial = source_denials(
                connection,
                request.company_id,
                (request.document_id,),
                request.action,
                occurred_at,
            ).get(request.document_id)
            if denial is not None:
                decision = AccessDecision(request=request, allowed=False, reason=denial)
        return decision, version

    def _audit(
        self,
        connection: Connection,
        result: MemoryResult,
        occurred_at: datetime,
        credential_id: str | None,
    ) -> None:
        """Persist attempt, credential and result metadata without document content."""
        event = make_audit_event(result.decision, occurred_at=occurred_at)
        connection.execute(
            insert(db.audit_events).values(
                event_id=str(uuid4()),
                **asdict(event.request),
                allowed=event.allowed,
                reason=event.reason.value,
                occurred_at=event.occurred_at,
                result=result.outcome.value,
                result_version_id=result.version_id,
                result_version_number=result.version_number,
                replayed=result.replayed,
                credential_id=credential_id,
            )
        )

    def read_version(
        self,
        request: AccessRequest,
        *,
        occurred_at: datetime,
        credential_id: str | None = None,
    ) -> MemoryResult:
        """Return an authorized version only after its required audit commits."""
        _check_call(request, Action.READ, occurred_at, credential_id)
        with self._transaction() as connection:
            decision, version = self._decision(
                connection, request, credential_id, occurred_at
            )
            if decision.allowed:
                assert version is not None
                result = MemoryResult(
                    decision=decision,
                    outcome=MemoryOutcome.READ,
                    version=version,
                    version_id=version.id,
                    version_number=version.number,
                )
            else:
                result = MemoryResult(decision=decision, outcome=MemoryOutcome.DENIED)
            self._audit(connection, result, occurred_at, credential_id)
        return result

    def write_version(
        self,
        request: AccessRequest,
        *,
        new_version: DocumentVersion,
        expected_number: int,
        occurred_at: datetime,
        credential_id: str | None = None,
    ) -> MemoryResult:
        """Append one version and atomically save its audit and idempotency record."""
        _check_call(request, Action.WRITE, occurred_at, credential_id)
        if not isinstance(new_version, DocumentVersion):
            raise TypeError("new_version must be a DocumentVersion")
        if type(expected_number) is not int or not 1 <= expected_number < 1_000_000:
            raise ValueError("expected_number must be an integer from 1 to 999999")
        if (
            new_version.company_id != request.company_id
            or new_version.document_id != request.document_id
            or new_version.number != expected_number + 1
            or not new_version.active
        ):
            raise ValueError(
                "new_version must be the next active revision of the target"
            )
        payload = json.dumps(
            {
                "request": asdict(request),
                "version": asdict(new_version),
                "expected_number": expected_number,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self._transaction() as connection:
            decision, base_version = self._decision(
                connection, request, credential_id, occurred_at
            )
            result = self._write(
                connection,
                decision,
                base_version,
                new_version,
                expected_number,
                fingerprint,
            )
            self._audit(connection, result, occurred_at, credential_id)
        return result

    def _write(
        self,
        connection: Connection,
        decision: AccessDecision,
        base_version: DocumentVersion | None,
        new_version: DocumentVersion,
        expected_number: int,
        fingerprint: str,
    ) -> MemoryResult:
        """Resolve retries and optimistic conflicts while holding the company lock."""
        if not decision.allowed:
            return MemoryResult(decision=decision, outcome=MemoryOutcome.DENIED)
        request = decision.request
        previous = (
            connection.execute(
                select(db.write_requests).where(
                    db.write_requests.c.company_id == request.company_id,
                    db.write_requests.c.employee_id == request.employee_id,
                    db.write_requests.c.agent_id == request.agent_id,
                    db.write_requests.c.request_id == request.request_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        if previous is not None:
            if previous["fingerprint"] != fingerprint:
                return MemoryResult(
                    decision=decision,
                    outcome=MemoryOutcome.IDEMPOTENCY_CONFLICT,
                )
            return MemoryResult(
                decision=decision,
                outcome=MemoryOutcome.WRITTEN,
                version_id=previous["result_version_id"],
                version_number=previous["result_version_number"],
                replayed=True,
            )
        current_number = connection.scalar(
            select(func.max(db.versions.c.number)).where(
                db.versions.c.company_id == request.company_id,
                db.versions.c.document_id == request.document_id,
            )
        )
        collision = _row(connection, db.versions, request.company_id, new_version.id)
        if (
            base_version is None
            or base_version.number != expected_number
            or current_number != expected_number
            or collision is not None
        ):
            return MemoryResult(
                decision=decision, outcome=MemoryOutcome.VERSION_CONFLICT
            )
        connection.execute(insert(db.versions).values(**asdict(new_version)))
        connection.execute(
            insert(db.write_requests).values(
                company_id=request.company_id,
                employee_id=request.employee_id,
                agent_id=request.agent_id,
                request_id=request.request_id,
                fingerprint=fingerprint,
                document_id=request.document_id,
                result_version_id=new_version.id,
                result_version_number=new_version.number,
            )
        )
        return MemoryResult(
            decision=decision,
            outcome=MemoryOutcome.WRITTEN,
            version_id=new_version.id,
            version_number=new_version.number,
        )

    def _scope_decision(
        self,
        connection: Connection,
        request: ScopeRequest,
        credential_id: str | None,
        occurred_at: datetime,
    ) -> tuple[ReasonCode, ScopeSnapshot | None]:
        """Lock the company, recheck the credential and evaluate project-wide access."""
        company_row = (
            connection.execute(
                select(db.companies)
                .where(db.companies.c.id == request.company_id)
                .with_for_update()
            )
            .mappings()
            .one_or_none()
        )
        if credential_id is not None and not credential_is_current(
            connection, request, credential_id, occurred_at
        ):
            return ReasonCode.CREDENTIAL_UNAVAILABLE, None
        project_row = _row(
            connection, db.projects, request.company_id, request.project_id
        )
        employee_row = _row(
            connection, db.employees, request.company_id, request.employee_id
        )
        agent_row = _row(connection, db.agents, request.company_id, request.agent_id)
        delegation_row = (
            connection.execute(
                select(db.delegations).where(
                    db.delegations.c.company_id == request.company_id,
                    db.delegations.c.project_id == request.project_id,
                    db.delegations.c.employee_id == request.employee_id,
                    db.delegations.c.agent_id == request.agent_id,
                )
            )
            .mappings()
            .one_or_none()
        )
        company = Company(**dict(company_row)) if company_row is not None else None
        project = Project(**dict(project_row)) if project_row is not None else None
        employee = Employee(**dict(employee_row)) if employee_row is not None else None
        agent = Agent(**dict(agent_row)) if agent_row is not None else None
        delegation = (
            Delegation(**_actions(delegation_row))
            if delegation_row is not None
            else None
        )
        reason = evaluate_scope(
            request,
            company=company,
            project=project,
            employee=employee,
            agent=agent,
            delegation=delegation,
        )
        if reason is not ReasonCode.ALLOWED:
            return reason, None
        assert company and project and employee and agent and delegation
        return reason, ScopeSnapshot(
            company=company,
            project=project,
            employee=employee,
            agent=agent,
            delegation=delegation,
        )

    def _version_reasons(
        self,
        connection: Connection,
        request: ScopeRequest,
        targets: Iterable[tuple[str, str]],
        credential_id: str | None,
        occurred_at: datetime,
    ) -> dict[str, ReasonCode]:
        """Reevaluate each distinct (document, version) pair with fresh reads."""
        reasons: dict[str, ReasonCode] = {}
        for document_id, version_id in targets:
            if version_id not in reasons:
                decision, _ = self._decision(
                    connection,
                    AccessRequest(
                        request_id=request.request_id,
                        company_id=request.company_id,
                        project_id=request.project_id,
                        employee_id=request.employee_id,
                        agent_id=request.agent_id,
                        document_id=document_id,
                        version_id=version_id,
                        action=Action.READ,
                    ),
                    credential_id,
                    occurred_at,
                )
                reasons[version_id] = decision.reason
        return reasons

    def _still_readable(
        self,
        connection: Connection,
        request: ScopeRequest,
        selected: tuple[RankedFragment, ...],
        credential_id: str | None,
        occurred_at: datetime,
    ) -> tuple[RankedFragment, ...]:
        """Drop selected fragments whose version lost access since ranking."""
        reasons = self._version_reasons(
            connection,
            request,
            (
                (entry.candidate.document_id, entry.candidate.version_id)
                for entry in selected
            ),
            credential_id,
            occurred_at,
        )
        return tuple(
            entry
            for entry in selected
            if reasons[entry.candidate.version_id] is ReasonCode.ALLOWED
        )

    def search_context(
        self,
        request: ScopeRequest,
        *,
        query: str,
        max_items: int,
        occurred_at: datetime,
        credential_id: str | None = None,
    ) -> ContextResult:
        """Filter documents by current rights, then rank, recheck, audit and commit."""
        _check_scope_call(request, occurred_at, credential_id)
        terms = query_terms(query)
        if type(max_items) is not int or not 1 <= max_items <= 10:
            raise ValueError("max_items must be an integer from 1 to 10")
        with self._transaction() as connection:
            reason, scope = self._scope_decision(
                connection, request, credential_id, occurred_at
            )
            if scope is None:
                result = ContextResult(
                    request=request, outcome=ContextOutcome.DENIED, reason=reason
                )
            else:
                versions, truncated = authorized_versions(
                    connection, request, scope, occurred_at
                )
                candidates = tuple(
                    Candidate(
                        document_id=version.document_id,
                        version_id=version.id,
                        version_number=version.number,
                        source=version.source,
                        fragment=fragment,
                    )
                    for version in versions
                    for fragment in split_fragments(version.content)
                )
                selected = select_context(rank(terms, candidates), max_items=max_items)
                kept = self._still_readable(
                    connection, request, selected, credential_id, occurred_at
                )
                result = ContextResult(
                    request=request,
                    outcome=ContextOutcome.FOUND
                    if kept
                    else ContextOutcome.NO_AUTHORIZED_EVIDENCE,
                    reason=ReasonCode.ALLOWED,
                    items=tuple(
                        ContextItem(
                            document_id=entry.candidate.document_id,
                            version_id=entry.candidate.version_id,
                            version_number=entry.candidate.version_number,
                            source=entry.candidate.source,
                            fragment=entry.candidate.fragment.ordinal,
                            start=entry.candidate.fragment.start,
                            end=entry.candidate.fragment.end,
                            score=entry.score,
                            text=entry.candidate.fragment.text,
                        )
                        for entry in kept
                    ),
                    truncated=truncated,
                )
            record_context(
                connection,
                result,
                query_terms=len(terms),
                query=query,
                max_items=max_items,
                occurred_at=occurred_at,
                credential_id=credential_id,
            )
        return result

    def check_scope(
        self,
        request: ScopeRequest,
        *,
        occurred_at: datetime,
        credential_id: str | None = None,
    ) -> ReasonCode:
        """Tell whether a project-wide read may start; finish_run audits the outcome."""
        _check_scope_call(request, occurred_at, credential_id)
        with self._transaction() as connection:
            reason, _ = self._scope_decision(
                connection, request, credential_id, occurred_at
            )
        return reason

    def finish_run(
        self,
        request: ScopeRequest,
        run: AgentRun,
        *,
        question: str,
        occurred_at: datetime,
        credential_id: str | None = None,
    ) -> AgentRun:
        """Recheck scope and every cited version, then record the run and commit.

        The answer is derived from the cited passages, so it is withheld when the
        scope or any cited version is no longer readable.
        """
        _check_scope_call(request, occurred_at, credential_id)
        if not isinstance(run, AgentRun):
            raise TypeError("run must be an AgentRun")
        check_question(question)
        with self._transaction() as connection:
            reason, _ = self._scope_decision(
                connection, request, credential_id, occurred_at
            )
            final = run
            if reason is not ReasonCode.ALLOWED:
                final = replace(
                    run,
                    outcome=RunOutcome.DENIED,
                    reason=reason,
                    answer=None,
                    citations=(),
                )
            elif run.outcome is RunOutcome.ANSWERED:
                reasons = self._version_reasons(
                    connection,
                    request,
                    ((item.document_id, item.version_id) for item in run.citations),
                    credential_id,
                    occurred_at,
                )
                lost = next(
                    (
                        value
                        for value in reasons.values()
                        if value is not ReasonCode.ALLOWED
                    ),
                    None,
                )
                if lost is not None:
                    final = replace(
                        run,
                        outcome=RunOutcome.EVIDENCE_REVOKED,
                        reason=lost,
                        answer=None,
                        citations=(),
                    )
            record_run(
                connection,
                request,
                final,
                question=question,
                occurred_at=occurred_at,
                credential_id=credential_id,
            )
        return final
