"""Run the memory service as its least-privilege role on actual PostgreSQL."""

import secrets
from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.application.agent import RunOutcome, run_agent
from app.application.context import ContextResult
from app.application.memory import MemoryOutcome
from app.application.offline_model import OfflineProvider
from app.application.source_sync import SyncOutcome
from app.config import Settings
from app.domain.models import Action, ScopeRequest
from app.domain.source import SourceSettings
from app.persistence import schema
from app.persistence.credentials import issue_credential
from app.persistence.database import make_engine
from app.persistence.intake import IntakeGrant, IntakeItem, ingest_documents
from app.persistence.memory import MemoryStore
from app.persistence.sources import register_source, synchronize
from tests.persistence.conftest import TIMESTAMP, PersistenceCase
from tests.test_source_sync import FakeSource

pytestmark = pytest.mark.postgres

APP_ROLE = "agentic_memory_app"
LOGIN_ROLE = "agentic_app_test"
READ = {"SELECT"}
# The only privileges the memory service holds; every table needs an entry.
EXPECTED = {
    "companies": {"SELECT"},
    "projects": READ,
    "employees": READ,
    "agents": READ,
    "agent_credentials": READ,
    "delegations": READ,
    "documents": READ,
    "grants": READ,
    "sources": READ,
    "source_documents": READ,
    "alembic_version": READ,
    "versions": {"SELECT", "INSERT"},
    "write_requests": {"SELECT", "INSERT"},
    "audit_events": {"INSERT"},
    "context_requests": {"INSERT"},
    "context_items": {"INSERT"},
    "agent_runs": {"INSERT"},
    "agent_run_searches": {"INSERT"},
    "agent_run_citations": {"INSERT"},
    "source_accounts": set(),
    "source_sync_runs": set(),
}
PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)


@pytest.fixture
def app_engine(engine: Engine, postgres_url: str) -> Iterator[Engine]:
    """Create a login role in the service group and connect as it."""
    password = secrets.token_hex(16)
    with engine.begin() as connection:
        connection.execute(text(f"DROP ROLE IF EXISTS {LOGIN_ROLE}"))
        connection.execute(
            text(
                f"CREATE ROLE {LOGIN_ROLE} LOGIN PASSWORD '{password}' "
                f"IN ROLE {APP_ROLE}"
            )
        )
    url = make_url(postgres_url).set(username=LOGIN_ROLE, password=password)
    restricted = make_engine(url.render_as_string(hide_password=False))
    try:
        yield restricted
    finally:
        restricted.dispose()
        with engine.begin() as connection:
            connection.execute(text(f"DROP ROLE {LOGIN_ROLE}"))


def test_privileges_match_the_documented_matrix(engine: Engine) -> None:
    """Each table has exactly the expected privileges and no table is forgotten."""
    tables = set(schema.metadata.tables) | {"alembic_version"}
    assert set(EXPECTED) == tables
    with engine.connect() as connection:
        for table, expected in EXPECTED.items():
            granted = {
                privilege
                for privilege in PRIVILEGES
                if connection.scalar(
                    text("SELECT has_table_privilege(:role, :table, :privilege)"),
                    {"role": APP_ROLE, "table": table, "privilege": privilege},
                )
            }
            assert granted == expected, table
        columns = {
            column: connection.scalar(
                text(
                    "SELECT has_column_privilege(:role, 'companies', :column, 'UPDATE')"
                ),
                {"role": APP_ROLE, "column": column},
            )
            for column in ("id", "name", "active")
        }
        assert columns == {"id": False, "name": True, "active": False}
        assert not connection.scalar(
            text("SELECT has_schema_privilege(:role, 'public', 'CREATE')"),
            {"role": APP_ROLE},
        )
        assert not connection.scalar(
            text(
                "SELECT rolsuper OR rolcreaterole OR rolcanlogin FROM pg_roles "
                "WHERE rolname = :role"
            ),
            {"role": APP_ROLE},
        )


def _ingest(case: PersistenceCase) -> None:
    """Import one readable fictional text as trusted setup."""
    ingest_documents(
        case.engine,
        company_id="alpha",
        project_id="finance",
        items=(
            IntakeItem(
                document_id="travel",
                title="travel",
                source="file:travel.txt",
                raw=b"Travel limit is 500 credits per trip.",
            ),
        ),
        grants=(
            IntakeGrant(
                employee_id="employee-finance", actions=frozenset({Action.READ})
            ),
        ),
    )


def _scope(request_id: str) -> ScopeRequest:
    """Build the finance agent's project-wide read request."""
    return ScopeRequest(
        request_id=request_id,
        company_id="alpha",
        project_id="finance",
        employee_id="employee-finance",
        agent_id="agent-finance",
        action=Action.READ,
    )


def test_every_memory_operation_works_as_the_service_role(
    case: PersistenceCase, app_engine: Engine
) -> None:
    """Reads, appends, replays, searches and answers need no more than the grants."""
    _ingest(case)
    store = MemoryStore(app_engine)
    read = store.read_version(case.request, occurred_at=TIMESTAMP)
    assert read.outcome is MemoryOutcome.READ
    write = case.write_request()
    first = store.write_version(
        write, new_version=case.new_version, expected_number=1, occurred_at=TIMESTAMP
    )
    replay = store.write_version(
        write, new_version=case.new_version, expected_number=1, occurred_at=TIMESTAMP
    )
    assert first.outcome is replay.outcome is MemoryOutcome.WRITTEN
    assert replay.replayed
    found = store.search_context(
        _scope("search-1"), query="travel limit", max_items=3, occurred_at=TIMESTAMP
    )
    assert [item.document_id for item in found.items] == ["travel"]
    calls = iter(range(1, 5))

    def search(query: str, max_items: int) -> ContextResult:
        """Search as the run's agent."""
        return store.search_context(
            _scope(f"run-search-{next(calls)}"),
            query=query,
            max_items=max_items,
            occurred_at=TIMESTAMP,
        )

    assert store.check_scope(_scope("run-1"), occurred_at=TIMESTAMP).value == "allowed"
    draft = run_agent(
        "What is the travel limit?", provider=OfflineProvider(), search=search
    )
    final = store.finish_run(
        _scope("run-1"),
        draft,
        question="What is the travel limit?",
        occurred_at=TIMESTAMP,
    )
    assert final.outcome is RunOutcome.ANSWERED


def test_http_service_runs_as_the_service_role(
    case: PersistenceCase, app_engine: Engine
) -> None:
    """Authentication, readiness and every route work with the restricted login."""
    from app.main import create_app

    _ingest(case)
    token = issue_credential(
        case.engine,
        company_id="alpha",
        credential_id="cred-role",
        agent_id="agent-finance",
        issued_at=TIMESTAMP - timedelta(minutes=5),
        expires_at=TIMESTAMP + timedelta(hours=1),
    )
    headers = {"Authorization": f"Bearer {token}"}
    application = create_app(Settings(), engine=app_engine, clock=lambda: TIMESTAMP)
    with TestClient(application) as client:
        assert client.get("/ready").json()["status"] == "ready"
        version = client.get(
            "/memory/projects/finance/documents/finance-policy/versions/finance-policy-v1",
            headers=headers,
        )
        assert version.status_code == 200
        appended = client.post(
            "/memory/projects/finance/documents/finance-policy/versions",
            headers={**headers, "Idempotency-Key": "role-write"},
            json={
                "base_version_id": "finance-policy-v1",
                "expected_number": 1,
                "content": "Fictional revision two.",
            },
        )
        assert appended.status_code == 201
        context = client.post(
            "/memory/projects/finance/context",
            headers=headers,
            json={"query": "travel limit", "max_items": 3},
        )
        assert context.status_code == 200
        answer = client.post(
            "/memory/projects/finance/answers",
            headers=headers,
            json={"question": "What is the travel limit?"},
        )
        assert answer.json()["status"] == "answered"


@pytest.mark.parametrize(
    "statement",
    [
        'UPDATE grants SET actions = \'["read", "write"]\'::jsonb',
        "INSERT INTO grants (company_id, id, project_id, employee_id, document_id, "
        "actions) VALUES ('alpha', 'g', 'finance', 'employee-finance', "
        "'finance-policy', '[\"read\"]')",
        "UPDATE delegations SET active = true",
        "UPDATE agent_credentials SET expires_at = expires_at + interval '1 year'",
        "UPDATE versions SET content = 'forged'",
        "DELETE FROM audit_events",
        "UPDATE audit_events SET allowed = true",
        "SELECT count(*) FROM audit_events",
        "TRUNCATE context_requests CASCADE",
        "UPDATE sources SET confirmed_at = now()",
        "SELECT count(*) FROM source_accounts",
        "INSERT INTO source_sync_runs (run_id) VALUES ('x')",
        "UPDATE companies SET active = false",
        "UPDATE companies SET name = 'Renamed'",
        "CREATE TABLE stolen (id int)",
        "DROP TABLE documents",
    ],
)
def test_the_service_role_cannot_change_rights_history_or_schema(
    case: PersistenceCase, app_engine: Engine, statement: str
) -> None:
    """A compromised service can neither widen access nor rewrite history."""
    with pytest.raises(DBAPIError) as raised, app_engine.begin() as connection:
        connection.execute(text(statement))
    assert raised.value.orig is not None
    assert getattr(raised.value.orig, "sqlstate", None) == "42501"


def test_owner_keeps_administrative_updates(case: PersistenceCase) -> None:
    """The guard trigger blocks only the service role, not trusted setup."""
    with case.engine.begin() as connection:
        connection.execute(
            text("UPDATE companies SET name = 'Alpha renamed' WHERE id = 'alpha'")
        )
        assert (
            connection.scalar(text("SELECT name FROM companies WHERE id = 'alpha'"))
            == "Alpha renamed"
        )


def test_the_service_role_cannot_run_a_source_sync(
    case: PersistenceCase, app_engine: Engine
) -> None:
    """Synchronization is trusted operator work and fails as the service role."""
    settings = SourceSettings(
        id="handbook",
        company_id="alpha",
        project_id="finance",
        base_url="https://handbook.alpha.example",
        space="alpha-handbook",
        document_prefix="hb-",
        max_age_seconds=900,
    )
    register_source(case.engine, settings, {"u-fin": "employee-finance"})
    source = FakeSource({None: ([], None)})
    with pytest.raises(DBAPIError):
        synchronize(
            app_engine,
            lambda value: source,
            company_id="alpha",
            source_id="handbook",
            clock=lambda: TIMESTAMP,
        )
    owner = synchronize(
        case.engine,
        lambda value: source,
        company_id="alpha",
        source_id="handbook",
        clock=lambda: TIMESTAMP,
    )
    assert owner.outcome is SyncOutcome.SUCCEEDED
