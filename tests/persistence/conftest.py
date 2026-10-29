"""Create migrated disposable PostgreSQL fixtures containing fictional data only."""

import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, Table, func, select, text
from sqlalchemy.engine import RowMapping, make_url
from sqlalchemy.exc import ArgumentError

from app.domain.models import AccessRequest, Action, DocumentVersion
from app.persistence import schema
from app.persistence.database import make_engine, migrate
from app.persistence.memory import MemoryStore

TIMESTAMP = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def require_test_database_url(value: str) -> str:
    """Refuse nonlocal or nondisposable databases before executing destructive SQL."""
    try:
        url = make_url(value)
    except ArgumentError:
        raise ValueError(
            "TEST_DATABASE_URL must be a valid disposable database URL"
        ) from None
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in {"127.0.0.1", "localhost", "::1"}
        or not url.database
        or not url.database.endswith("_test")
        or bool(url.query)
    ):
        raise ValueError("PostgreSQL tests require a loopback database ending in _test")
    return value


@pytest.fixture(scope="session")
def postgres_url() -> str:
    """Skip optional local integration tests only when no database was configured."""
    value = os.environ.get("TEST_DATABASE_URL")
    if value is None:
        pytest.skip("Set TEST_DATABASE_URL to run disposable PostgreSQL tests")
    return require_test_database_url(value)


@pytest.fixture(scope="session")
def pg_engine(postgres_url: str) -> Iterator[Engine]:
    """Apply real migrations and dispose the connection pool afterward."""
    migrate(postgres_url)
    engine = make_engine(postgres_url)
    yield engine
    engine.dispose()


@pytest.fixture
def engine(pg_engine: Engine) -> Iterator[Engine]:
    """Reset only the guarded test database's known application tables per scenario."""
    with pg_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE source_sync_runs, source_documents, source_accounts, "
                "sources, agent_run_citations, agent_run_searches, agent_runs, "
                "context_items, context_requests, write_requests, "
                "audit_events, grants, versions, documents, "
                "delegations, agent_credentials, agents, employees, projects, "
                "companies CASCADE"
            )
        )
    yield pg_engine


@dataclass(frozen=True)
class PersistenceCase:
    """Keep a migrated fictional snapshot and its expected version values together."""

    engine: Engine
    store: MemoryStore
    request: AccessRequest
    initial_version: DocumentVersion
    new_version: DocumentVersion

    def write_request(self, request_id: str = "write-1") -> AccessRequest:
        """Request a write against the original version with an explicit attempt ID."""
        return replace(self.request, action=Action.WRITE, request_id=request_id)


@pytest.fixture
def case(engine: Engine) -> PersistenceCase:
    """Persist a small finance project with separate employee and agent permissions."""
    with engine.begin() as connection:
        connection.execute(
            schema.companies.insert(), {"id": "alpha", "name": "Alpha", "active": True}
        )
        connection.execute(
            schema.projects.insert(),
            {"company_id": "alpha", "id": "finance", "name": "Finance", "active": True},
        )
        connection.execute(
            schema.employees.insert(),
            {
                "company_id": "alpha",
                "id": "employee-finance",
                "name": "Fictional analyst",
                "active": True,
            },
        )
        connection.execute(
            schema.agents.insert(),
            {
                "company_id": "alpha",
                "id": "agent-finance",
                "employee_id": "employee-finance",
                "name": "Finance assistant",
                "active": True,
            },
        )
        connection.execute(
            schema.delegations.insert(),
            {
                "company_id": "alpha",
                "id": "delegation-1",
                "project_id": "finance",
                "employee_id": "employee-finance",
                "agent_id": "agent-finance",
                "actions": ["read", "write"],
                "active": True,
            },
        )
        connection.execute(
            schema.documents.insert(),
            {
                "company_id": "alpha",
                "id": "finance-policy",
                "project_id": "finance",
                "title": "Fictional finance policy",
                "active": True,
            },
        )
        connection.execute(
            schema.versions.insert(),
            {
                "company_id": "alpha",
                "id": "finance-policy-v1",
                "document_id": "finance-policy",
                "number": 1,
                "source": "fixture:finance-policy",
                "content": "Fictional private revision one.",
                "active": True,
            },
        )
        connection.execute(
            schema.grants.insert(),
            {
                "company_id": "alpha",
                "id": "grant-1",
                "project_id": "finance",
                "employee_id": "employee-finance",
                "document_id": "finance-policy",
                "actions": ["read", "write"],
                "active": True,
            },
        )
    initial = DocumentVersion(
        id="finance-policy-v1",
        company_id="alpha",
        document_id="finance-policy",
        number=1,
        source="fixture:finance-policy",
        content="Fictional private revision one.",
    )
    return PersistenceCase(
        engine=engine,
        store=MemoryStore(engine),
        request=AccessRequest(
            request_id="read-1",
            company_id="alpha",
            project_id="finance",
            employee_id="employee-finance",
            agent_id="agent-finance",
            document_id="finance-policy",
            version_id=initial.id,
            action=Action.READ,
        ),
        initial_version=initial,
        new_version=replace(
            initial,
            id="finance-policy-v2",
            number=2,
            content="Fictional private revision two.",
        ),
    )


def row_count(engine: Engine, table: Table) -> int:
    """Count persisted rows without deriving an expected policy outcome."""
    with engine.connect() as connection:
        return int(
            connection.execute(select(func.count()).select_from(table)).scalar_one()
        )


def recorded_attempts(engine: Engine) -> tuple[RowMapping, ...]:
    """Read audit metadata for assertions without fetching any document text."""
    with engine.connect() as connection:
        return tuple(connection.execute(select(schema.audit_events)).mappings())


def revoke_employee_grant(engine: Engine) -> None:
    """Simulate a trusted administrator changing rights under the company lock."""
    with engine.begin() as connection:
        connection.execute(
            select(schema.companies)
            .where(schema.companies.c.id == "alpha")
            .with_for_update()
        )
        connection.execute(
            schema.grants.update()
            .where(schema.grants.c.company_id == "alpha")
            .values(active=False)
        )
