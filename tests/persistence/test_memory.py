"""Verify durable memory behavior and audit atomicity on actual PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from typing import Any, cast

import pytest
from sqlalchemy import Connection, event, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.application.memory import MemoryOutcome
from app.domain.models import ReasonCode
from app.persistence import schema
from app.persistence.database import make_engine
from app.persistence.memory import MemoryStore
from tests.persistence.conftest import (
    TIMESTAMP,
    PersistenceCase,
    recorded_attempts,
    revoke_employee_grant,
    row_count,
)

pytestmark = pytest.mark.postgres


def test_read_returns_saved_version_and_records_attempt(case: PersistenceCase) -> None:
    """A permitted read returns the exact stored revision after durable audit."""
    result = case.store.read_version(case.request, occurred_at=TIMESTAMP)
    assert result.outcome is MemoryOutcome.READ
    assert result.decision.allowed is True
    assert result.version == case.initial_version
    attempts = recorded_attempts(case.engine)
    assert len(attempts) == 1
    assert attempts[0]["request_id"] == case.request.request_id
    assert attempts[0]["allowed"] is True
    assert attempts[0]["result"] == "read"
    assert attempts[0]["occurred_at"] == TIMESTAMP


def test_a_new_engine_reads_existing_data(
    case: PersistenceCase, postgres_url: str
) -> None:
    """Closing the original connection pool does not erase committed memory."""
    case.engine.dispose()
    fresh_engine = make_engine(postgres_url)
    try:
        result = MemoryStore(fresh_engine).read_version(
            case.request, occurred_at=TIMESTAMP
        )
        assert result.version == case.initial_version
        assert row_count(fresh_engine, schema.audit_events) == 1
    finally:
        fresh_engine.dispose()


def test_write_appends_version_and_preserves_old_content(case: PersistenceCase) -> None:
    """A mutation commits its revision, audit row and replay record together."""
    result = case.store.write_version(
        case.write_request(),
        new_version=case.new_version,
        expected_number=1,
        occurred_at=TIMESTAMP,
    )
    assert result.outcome is MemoryOutcome.WRITTEN
    assert result.version is None
    assert result.version_id == case.new_version.id
    assert result.version_number == 2
    assert result.replayed is False
    assert row_count(case.engine, schema.versions) == 2
    assert row_count(case.engine, schema.write_requests) == 1
    with case.engine.connect() as connection:
        old = (
            connection.execute(
                select(schema.versions).where(
                    schema.versions.c.id == case.initial_version.id
                )
            )
            .mappings()
            .one()
        )
    assert old["number"] == 1
    assert old["content"] == case.initial_version.content
    attempts = recorded_attempts(case.engine)
    assert len(attempts) == 1
    assert attempts[0]["result"] == "written"
    assert attempts[0]["result_version_id"] == case.new_version.id


def test_identical_retry_does_not_duplicate_a_version(case: PersistenceCase) -> None:
    """A repeated write returns its original metadata but audits the new attempt."""
    first = case.store.write_version(
        case.write_request(),
        new_version=case.new_version,
        expected_number=1,
        occurred_at=TIMESTAMP,
    )
    retry = case.store.write_version(
        case.write_request(),
        new_version=case.new_version,
        expected_number=1,
        occurred_at=TIMESTAMP,
    )
    assert first.outcome is retry.outcome is MemoryOutcome.WRITTEN
    assert first.replayed is False
    assert retry.replayed is True
    assert retry.version is None
    assert retry.version_id == case.new_version.id
    assert row_count(case.engine, schema.versions) == 2
    assert row_count(case.engine, schema.write_requests) == 1
    attempts = recorded_attempts(case.engine)
    assert len(attempts) == 2
    assert sum(bool(attempt["replayed"]) for attempt in attempts) == 1


@pytest.mark.parametrize(
    "field, value",
    [("content", "Different fictional payload."), ("id", "different-v2")],
)
def test_same_request_key_with_changed_payload_is_audited_conflict(
    case: PersistenceCase, field: str, value: str
) -> None:
    """An idempotency key cannot be reused for another intended mutation."""
    case.store.write_version(
        case.write_request(),
        new_version=case.new_version,
        expected_number=1,
        occurred_at=TIMESTAMP,
    )
    changed = (
        replace(case.new_version, content=value)
        if field == "content"
        else replace(case.new_version, id=value)
    )
    result = case.store.write_version(
        case.write_request(),
        new_version=changed,
        expected_number=1,
        occurred_at=TIMESTAMP,
    )
    assert result.outcome is MemoryOutcome.IDEMPOTENCY_CONFLICT
    assert result.version is None
    assert row_count(case.engine, schema.versions) == 2
    assert row_count(case.engine, schema.write_requests) == 1
    assert {entry["result"] for entry in recorded_attempts(case.engine)} == {
        "written",
        "idempotency_conflict",
    }


def test_retry_rechecks_current_employee_permission(case: PersistenceCase) -> None:
    """A stored successful request cannot bypass a later revocation."""
    case.store.write_version(
        case.write_request(),
        new_version=case.new_version,
        expected_number=1,
        occurred_at=TIMESTAMP,
    )
    revoke_employee_grant(case.engine)
    result = case.store.write_version(
        case.write_request(),
        new_version=case.new_version,
        expected_number=1,
        occurred_at=TIMESTAMP,
    )
    assert result.outcome is MemoryOutcome.DENIED
    assert result.decision.reason is ReasonCode.EMPLOYEE_PERMISSION_MISSING
    assert result.version is None
    assert result.replayed is False
    assert row_count(case.engine, schema.versions) == 2
    assert row_count(case.engine, schema.audit_events) == 2


def test_stale_distinct_write_is_audited_version_conflict(
    case: PersistenceCase,
) -> None:
    """Another request cannot append from an obsolete base revision."""
    case.store.write_version(
        case.write_request(),
        new_version=case.new_version,
        expected_number=1,
        occurred_at=TIMESTAMP,
    )
    result = case.store.write_version(
        case.write_request("write-other"),
        new_version=replace(case.new_version, id="another-v2"),
        expected_number=1,
        occurred_at=TIMESTAMP,
    )
    assert result.outcome is MemoryOutcome.VERSION_CONFLICT
    assert result.version is None
    assert row_count(case.engine, schema.versions) == 2
    assert row_count(case.engine, schema.write_requests) == 1
    assert {entry["result"] for entry in recorded_attempts(case.engine)} == {
        "written",
        "version_conflict",
    }


def test_existing_version_identifier_is_not_overwritten(case: PersistenceCase) -> None:
    """Version identity is immutable even if a caller proposes a new sequence number."""
    collision = replace(case.new_version, id=case.initial_version.id)
    result = case.store.write_version(
        case.write_request(),
        new_version=collision,
        expected_number=1,
        occurred_at=TIMESTAMP,
    )
    assert result.outcome is MemoryOutcome.VERSION_CONFLICT
    assert row_count(case.engine, schema.versions) == 1
    assert row_count(case.engine, schema.write_requests) == 0
    assert recorded_attempts(case.engine)[0]["result"] == "version_conflict"


def test_read_denial_has_no_content_and_is_persisted(case: PersistenceCase) -> None:
    """A revoked employee grant causes a durable denial instead of returning text."""
    revoke_employee_grant(case.engine)
    result = case.store.read_version(case.request, occurred_at=TIMESTAMP)
    assert result.outcome is MemoryOutcome.DENIED
    assert result.version is None
    assert result.decision.reason is ReasonCode.EMPLOYEE_PERMISSION_MISSING
    attempt = recorded_attempts(case.engine)[0]
    assert attempt["result"] == "denied"
    assert attempt["allowed"] is False


@pytest.mark.parametrize(
    "field", ["company_id", "employee_id", "agent_id", "document_id", "version_id"]
)
def test_missing_target_or_actor_is_recorded_without_foreign_key_failure(
    case: PersistenceCase, field: str
) -> None:
    """Unknown request metadata is auditable but cannot reveal existing content."""
    result = case.store.read_version(
        replace(case.request, **cast(dict[str, Any], {field: "unknown"})),
        occurred_at=TIMESTAMP,
    )
    assert result.outcome is MemoryOutcome.DENIED
    assert result.version is None
    assert row_count(case.engine, schema.audit_events) == 1


def test_request_project_field_cannot_grant_access(case: PersistenceCase) -> None:
    """A caller-supplied alternate project does not move the saved document."""
    with case.engine.begin() as connection:
        connection.execute(
            schema.projects.insert(),
            {
                "company_id": "alpha",
                "id": "engineering",
                "name": "Engineering",
                "active": True,
            },
        )
    result = case.store.read_version(
        replace(case.request, project_id="engineering"), occurred_at=TIMESTAMP
    )
    assert result.outcome is MemoryOutcome.DENIED
    assert result.version is None
    assert row_count(case.engine, schema.audit_events) == 1


def test_version_must_still_be_active_for_read(case: PersistenceCase) -> None:
    """A known revision ID cannot bypass the stored availability flag."""
    with case.engine.begin() as connection:
        connection.execute(
            schema.versions.update()
            .where(schema.versions.c.id == case.initial_version.id)
            .values(active=False)
        )
    result = case.store.read_version(case.request, occurred_at=TIMESTAMP)
    assert result.outcome is MemoryOutcome.DENIED
    assert result.decision.reason is ReasonCode.VERSION_UNAVAILABLE
    assert result.version is None


def test_audit_has_no_document_payload_columns(case: PersistenceCase) -> None:
    """Persisted history contains identifiers and outcomes rather than document text."""
    case.store.read_version(case.request, occurred_at=TIMESTAMP)
    attempt = recorded_attempts(case.engine)[0]
    assert not {"content", "source", "title", "payload"}.intersection(attempt)
    assert case.initial_version.content not in repr(dict(attempt))


@pytest.mark.parametrize("operation", ["read", "write"])
def test_audit_failure_prevents_business_success(
    case: PersistenceCase, operation: str
) -> None:
    """A real audit insert rejection blocks reads and rolls back mutation metadata."""
    with case.engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE audit_events ADD CONSTRAINT test_audit_failure "
                "CHECK (false) NOT VALID"
            )
        )
    try:
        with pytest.raises(SQLAlchemyError):
            if operation == "read":
                case.store.read_version(case.request, occurred_at=TIMESTAMP)
            else:
                case.store.write_version(
                    case.write_request(),
                    new_version=case.new_version,
                    expected_number=1,
                    occurred_at=TIMESTAMP,
                )
    finally:
        with case.engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE audit_events DROP CONSTRAINT test_audit_failure")
            )
    assert row_count(case.engine, schema.versions) == 1
    assert row_count(case.engine, schema.write_requests) == 0
    assert row_count(case.engine, schema.audit_events) == 0


@pytest.mark.parametrize("isolation_level", ["READ COMMITTED", "REPEATABLE READ"])
def test_concurrent_identical_writes_commit_once(
    case: PersistenceCase, isolation_level: str
) -> None:
    """Retries see committed winners even when the caller supplies repeatable read."""
    supplied_engine = case.engine.execution_options(isolation_level=isolation_level)
    with supplied_engine.connect() as connection:
        assert connection.get_isolation_level() == isolation_level
    store = MemoryStore(supplied_engine)
    observed_isolation: list[str] = []

    def observe_operation_isolation(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        """Inspect the actual server transaction used by each company lock query."""
        if "for update" in statement.lower():
            level = str(
                connection.exec_driver_sql("SHOW transaction_isolation").scalar_one()
            )
            observed_isolation.append(level)
            assert level == "read committed"

    event.listen(supplied_engine, "before_cursor_execute", observe_operation_isolation)
    start = Barrier(2)

    def write(_index: int) -> tuple[MemoryOutcome, bool]:
        """Execute one independent connection-backed write attempt."""
        start.wait(timeout=5)
        result = store.write_version(
            case.write_request(),
            new_version=case.new_version,
            expected_number=1,
            occurred_at=TIMESTAMP,
        )
        return result.outcome, result.replayed

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(write, range(2)))
    finally:
        event.remove(
            supplied_engine, "before_cursor_execute", observe_operation_isolation
        )
    assert observed_isolation == ["read committed", "read committed"]
    assert [outcome for outcome, _ in results] == [MemoryOutcome.WRITTEN] * 2
    assert sorted(replayed for _, replayed in results) == [False, True]
    assert row_count(case.engine, schema.versions) == 2
    assert row_count(case.engine, schema.write_requests) == 1
    assert row_count(case.engine, schema.audit_events) == 2


def test_concurrent_distinct_writes_detect_stale_base(case: PersistenceCase) -> None:
    """Only one of two different requests can extend the same current revision."""

    start = Barrier(2)

    def write(index: int) -> MemoryOutcome:
        """Attempt a unique version using the shared original expected number."""
        start.wait(timeout=5)
        return case.store.write_version(
            case.write_request(f"write-{index}"),
            new_version=replace(case.new_version, id=f"revision-{index}"),
            expected_number=1,
            occurred_at=TIMESTAMP,
        ).outcome

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, range(2)))
    assert set(results) == {MemoryOutcome.WRITTEN, MemoryOutcome.VERSION_CONFLICT}
    assert row_count(case.engine, schema.versions) == 2
    assert row_count(case.engine, schema.write_requests) == 1
    assert row_count(case.engine, schema.audit_events) == 2


def test_another_company_cannot_borrow_saved_alpha_permissions(
    case: PersistenceCase,
) -> None:
    """Valid foreign company/project records do not grant Alpha's employee rights."""
    with case.engine.begin() as connection:
        connection.execute(
            schema.companies.insert(), {"id": "beta", "name": "Beta", "active": True}
        )
        connection.execute(
            schema.projects.insert(),
            {
                "company_id": "beta",
                "id": "finance",
                "name": "Foreign finance",
                "active": True,
            },
        )
    result = case.store.read_version(
        replace(case.request, company_id="beta"), occurred_at=TIMESTAMP
    )
    assert result.outcome is MemoryOutcome.DENIED
    assert result.version is None
    assert result.decision.reason is ReasonCode.EMPLOYEE_UNAVAILABLE
    assert recorded_attempts(case.engine)[0]["company_id"] == "beta"
