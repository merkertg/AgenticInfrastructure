"""Exercise authenticated memory HTTP routes against actual PostgreSQL."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.engine import RowMapping

from app.api.memory import NOT_AVAILABLE, UNAUTHORIZED
from app.application.identity import Principal
from app.config import Settings
from app.domain.versions import derive_version_id
from app.persistence import schema
from app.persistence.credentials import (
    CredentialAuthenticator,
    generate_token,
    issue_credential,
)
from tests.persistence.conftest import (
    TIMESTAMP,
    PersistenceCase,
    recorded_attempts,
    row_count,
)

pytestmark = pytest.mark.postgres

FINANCE = "/memory/projects/finance/documents/finance-policy/versions"
FINANCE_V1 = f"{FINANCE}/finance-policy-v1"
ENGINEERING_V1 = (
    "/memory/projects/engineering/documents/engineering-notes/versions/"
    "engineering-notes-v1"
)
BETA_V1 = "/memory/projects/finance/documents/beta-plan/versions/beta-plan-v1"


@dataclass(frozen=True)
class HttpCase:
    """Hold a client and fictional bearer tokens for agents of two companies."""

    client: TestClient
    case: PersistenceCase
    finance: dict[str, str]
    engineer: dict[str, str]
    beta: dict[str, str]


def _bearer(token: str) -> dict[str, str]:
    """Build the only header the server accepts as caller identity."""
    return {"Authorization": f"Bearer {token}"}


def _seed_second_project_and_company(case: PersistenceCase) -> None:
    """Add an engineering agent in Alpha and a separate Beta company document."""
    with case.engine.begin() as connection:
        connection.execute(
            schema.projects.insert(),
            {"company_id": "alpha", "id": "engineering", "name": "Engineering"},
        )
        connection.execute(
            schema.employees.insert(),
            {"company_id": "alpha", "id": "employee-engineer", "name": "Engineer"},
        )
        connection.execute(
            schema.agents.insert(),
            {
                "company_id": "alpha",
                "id": "agent-engineer",
                "employee_id": "employee-engineer",
                "name": "Engineering assistant",
            },
        )
        connection.execute(
            schema.delegations.insert(),
            {
                "company_id": "alpha",
                "id": "delegation-engineer",
                "project_id": "engineering",
                "employee_id": "employee-engineer",
                "agent_id": "agent-engineer",
                "actions": ["read"],
            },
        )
        connection.execute(
            schema.documents.insert(),
            {
                "company_id": "alpha",
                "id": "engineering-notes",
                "project_id": "engineering",
                "title": "Fictional engineering notes",
            },
        )
        connection.execute(
            schema.versions.insert(),
            {
                "company_id": "alpha",
                "id": "engineering-notes-v1",
                "document_id": "engineering-notes",
                "number": 1,
                "source": "fixture:engineering-notes",
                "content": "Fictional engineering revision one.",
            },
        )
        connection.execute(
            schema.grants.insert(),
            {
                "company_id": "alpha",
                "id": "grant-engineer",
                "project_id": "engineering",
                "employee_id": "employee-engineer",
                "document_id": "engineering-notes",
                "actions": ["read", "write"],
            },
        )
        connection.execute(schema.companies.insert(), {"id": "beta", "name": "Beta"})
        connection.execute(
            schema.projects.insert(),
            {"company_id": "beta", "id": "finance", "name": "Beta finance"},
        )
        connection.execute(
            schema.employees.insert(),
            {"company_id": "beta", "id": "employee-finance", "name": "Beta analyst"},
        )
        connection.execute(
            schema.agents.insert(),
            {
                "company_id": "beta",
                "id": "agent-finance",
                "employee_id": "employee-finance",
                "name": "Beta assistant",
            },
        )
        connection.execute(
            schema.delegations.insert(),
            {
                "company_id": "beta",
                "id": "delegation-beta",
                "project_id": "finance",
                "employee_id": "employee-finance",
                "agent_id": "agent-finance",
                "actions": ["read", "write"],
            },
        )
        connection.execute(
            schema.documents.insert(),
            {
                "company_id": "beta",
                "id": "beta-plan",
                "project_id": "finance",
                "title": "Fictional Beta plan",
            },
        )
        connection.execute(
            schema.versions.insert(),
            {
                "company_id": "beta",
                "id": "beta-plan-v1",
                "document_id": "beta-plan",
                "number": 1,
                "source": "fixture:beta-plan",
                "content": "Fictional Beta private revision.",
            },
        )
        connection.execute(
            schema.grants.insert(),
            {
                "company_id": "beta",
                "id": "grant-beta",
                "project_id": "finance",
                "employee_id": "employee-finance",
                "document_id": "beta-plan",
                "actions": ["read", "write"],
            },
        )


def _issue(case: PersistenceCase, company_id: str, agent_id: str, name: str) -> str:
    """Issue a credential valid for one hour around the fixed request time."""
    return issue_credential(
        case.engine,
        company_id=company_id,
        credential_id=name,
        agent_id=agent_id,
        issued_at=TIMESTAMP - timedelta(minutes=5),
        expires_at=TIMESTAMP + timedelta(hours=1),
    )


@pytest.fixture
def http(case: PersistenceCase) -> Iterator[HttpCase]:
    """Serve the real application over the migrated test database at a fixed time."""
    from app.main import create_app

    _seed_second_project_and_company(case)
    finance = _issue(case, "alpha", "agent-finance", "cred-finance")
    engineer = _issue(case, "alpha", "agent-engineer", "cred-engineer")
    beta = _issue(case, "beta", "agent-finance", "cred-beta")
    application = create_app(Settings(), engine=case.engine, clock=lambda: TIMESTAMP)
    with TestClient(application) as client:
        yield HttpCase(
            client=client,
            case=case,
            finance=_bearer(finance),
            engineer=_bearer(engineer),
            beta=_bearer(beta),
        )


def _append(
    http: HttpCase,
    *,
    key: str = "append-1",
    content: str = "Fictional HTTP revision two.",
    expected_number: int = 1,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, object]]:
    """Send one append request and return its status and JSON body."""
    response = http.client.post(
        FINANCE,
        headers={**(headers or http.finance), "Idempotency-Key": key},
        json={
            "base_version_id": "finance-policy-v1",
            "expected_number": expected_number,
            "content": content,
        },
    )
    return response.status_code, response.json()


def _only_attempt(http: HttpCase) -> RowMapping:
    """Return the single audit row recorded by the scenario."""
    attempts = recorded_attempts(http.case.engine)
    assert len(attempts) == 1
    return attempts[0]


def test_authenticated_read_returns_version_and_audits_actor(http: HttpCase) -> None:
    """The allowed path goes through HTTP, credential lookup, policy and audit."""
    response = http.client.get(FINANCE_V1, headers=http.finance)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "document_id": "finance-policy",
        "version_id": "finance-policy-v1",
        "number": 1,
        "source": "fixture:finance-policy",
        "content": "Fictional private revision one.",
    }
    attempt = _only_attempt(http)
    assert attempt["company_id"] == "alpha"
    assert attempt["employee_id"] == "employee-finance"
    assert attempt["agent_id"] == "agent-finance"
    assert attempt["credential_id"] == "cred-finance"
    assert attempt["result"] == "read"
    assert attempt["occurred_at"] == TIMESTAMP


@pytest.mark.parametrize(
    "authorization",
    [None, "Basic YWdlbnQ6c2VjcmV0", "Bearer not-a-token", "Bearer generated"],
)
def test_bad_credentials_get_one_401_and_no_business_audit(
    http: HttpCase, authorization: str | None
) -> None:
    """Missing, malformed and unknown tokens are indistinguishable and not audited."""
    headers = {}
    if authorization == "Bearer generated":
        headers["Authorization"] = f"Bearer {generate_token()}"
    elif authorization is not None:
        headers["Authorization"] = authorization
    response = http.client.get(FINANCE_V1, headers=headers)
    assert response.status_code == 401
    assert response.json() == {"detail": UNAUTHORIZED}
    assert response.headers["www-authenticate"] == "Bearer"
    assert "Fictional" not in response.text
    assert row_count(http.case.engine, schema.audit_events) == 0


@pytest.mark.parametrize("change", ["revoked", "expired"])
def test_revoked_or_expired_credentials_are_rejected(
    http: HttpCase, change: str
) -> None:
    """Stored credential state is checked on every request, not cached."""
    assert http.client.get(FINANCE_V1, headers=http.finance).status_code == 200
    with http.case.engine.begin() as connection:
        connection.execute(
            schema.agent_credentials.update()
            .where(schema.agent_credentials.c.id == "cred-finance")
            .values(
                active=False
                if change == "revoked"
                else schema.agent_credentials.c.active,
                expires_at=TIMESTAMP
                if change == "expired"
                else schema.agent_credentials.c.expires_at,
            )
        )
    response = http.client.get(FINANCE_V1, headers=http.finance)
    assert response.status_code == 401
    assert response.json() == {"detail": UNAUTHORIZED}
    assert row_count(http.case.engine, schema.audit_events) == 1


def test_identity_headers_and_query_fields_cannot_select_an_employee(
    http: HttpCase,
) -> None:
    """Spoofed employee or agent hints are ignored in favor of the credential."""
    spoofed = {
        **http.engineer,
        "X-Employee-ID": "employee-finance",
        "X-Agent-ID": "agent-finance",
        "X-Company-ID": "alpha",
    }
    response = http.client.get(
        FINANCE_V1,
        headers=spoofed,
        params={"employee_id": "employee-finance", "agent_id": "agent-finance"},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": NOT_AVAILABLE}
    attempt = _only_attempt(http)
    assert attempt["employee_id"] == "employee-engineer"
    assert attempt["agent_id"] == "agent-engineer"
    assert attempt["credential_id"] == "cred-engineer"
    assert attempt["allowed"] is False


def test_write_body_cannot_carry_actor_or_provenance(http: HttpCase) -> None:
    """Extra identity or source fields are rejected before any memory operation."""
    response = http.client.post(
        FINANCE,
        headers={**http.engineer, "Idempotency-Key": "spoof-1"},
        json={
            "base_version_id": "finance-policy-v1",
            "expected_number": 1,
            "content": "Spoofed revision.",
            "employee_id": "employee-finance",
            "source": "fixture:trusted",
        },
    )
    assert response.status_code == 422
    assert row_count(http.case.engine, schema.versions) == 3
    assert row_count(http.case.engine, schema.audit_events) == 0


def test_foreign_company_document_looks_like_a_missing_one(http: HttpCase) -> None:
    """Another company's real document and an unknown ID give the same denial."""
    foreign = http.client.get(BETA_V1, headers=http.finance)
    missing = http.client.get(f"{FINANCE}/finance-policy-v9", headers=http.finance)
    unknown_document = http.client.get(
        "/memory/projects/finance/documents/nothing/versions/nothing-v1",
        headers=http.finance,
    )
    assert foreign.status_code == missing.status_code == unknown_document.status_code
    assert foreign.status_code == 404
    assert foreign.json() == missing.json() == unknown_document.json()
    assert "Beta" not in foreign.text
    assert [row["allowed"] for row in recorded_attempts(http.case.engine)] == [
        False
    ] * 3


def test_other_company_agent_cannot_read_alpha_with_matching_ids(
    http: HttpCase,
) -> None:
    """Beta's agent shares local IDs with Alpha's agent but stays in Beta."""
    response = http.client.get(FINANCE_V1, headers=http.beta)
    assert response.status_code == 404
    attempt = _only_attempt(http)
    assert attempt["company_id"] == "beta"
    assert attempt["credential_id"] == "cred-beta"
    assert attempt["reason"] == "document_unavailable"
    assert http.client.get(BETA_V1, headers=http.beta).status_code == 200


@pytest.mark.parametrize(
    ("headers_name", "path", "reason"),
    [
        ("finance", ENGINEERING_V1, "delegation_unavailable"),
        ("engineer", FINANCE_V1, "delegation_unavailable"),
        (
            "engineer",
            "/memory/projects/engineering/documents/finance-policy/versions/"
            "finance-policy-v1",
            "project_mismatch",
        ),
    ],
)
def test_other_project_is_denied_and_audited(
    http: HttpCase, headers_name: str, path: str, reason: str
) -> None:
    """Delegation and document project are both enforced from stored records."""
    response = http.client.get(path, headers=getattr(http, headers_name))
    assert response.status_code == 404
    assert response.json() == {"detail": NOT_AVAILABLE}
    assert _only_attempt(http)["reason"] == reason


def test_revoked_delegation_denies_a_valid_credential(http: HttpCase) -> None:
    """A valid token does not keep access after its agent's delegation is revoked."""
    with http.case.engine.begin() as connection:
        connection.execute(
            schema.delegations.update()
            .where(schema.delegations.c.id == "delegation-1")
            .values(active=False)
        )
    response = http.client.get(FINANCE_V1, headers=http.finance)
    assert response.status_code == 404
    assert _only_attempt(http)["reason"] == "delegation_unavailable"


def test_inactive_agent_or_employee_is_denied(http: HttpCase) -> None:
    """Authentication alone is not enough when the registration became inactive."""
    with http.case.engine.begin() as connection:
        connection.execute(
            schema.agents.update()
            .where(schema.agents.c.id == "agent-finance")
            .values(active=False)
        )
    assert http.client.get(FINANCE_V1, headers=http.finance).status_code == 404
    assert recorded_attempts(http.case.engine)[0]["reason"] == "agent_unavailable"


def test_agent_cannot_exceed_employee_or_delegated_rights(http: HttpCase) -> None:
    """Writes need both the employee grant and the agent's delegated action."""
    with http.case.engine.begin() as connection:
        connection.execute(
            schema.grants.update()
            .where(schema.grants.c.id == "grant-1")
            .values(actions=["read"])
        )
    status, body = _append(http)
    assert status == 404
    assert body == {"detail": NOT_AVAILABLE}
    response = http.client.post(
        "/memory/projects/engineering/documents/engineering-notes/versions",
        headers={**http.engineer, "Idempotency-Key": "eng-1"},
        json={
            "base_version_id": "engineering-notes-v1",
            "expected_number": 1,
            "content": "Engineer revision.",
        },
    )
    assert response.status_code == 404
    reasons = [row["reason"] for row in recorded_attempts(http.case.engine)]
    assert sorted(reasons) == ["action_not_delegated", "employee_permission_missing"]
    assert row_count(http.case.engine, schema.versions) == 3


def test_http_append_replays_and_reports_conflicts(http: HttpCase) -> None:
    """New, retried, changed and stale writes have distinct HTTP outcomes."""
    expected_id = derive_version_id("alpha", "finance-policy", 2)
    assert _append(http) == (
        201,
        {
            "document_id": "finance-policy",
            "version_id": expected_id,
            "number": 2,
            "replayed": False,
        },
    )
    assert _append(http) == (
        200,
        {
            "document_id": "finance-policy",
            "version_id": expected_id,
            "number": 2,
            "replayed": True,
        },
    )
    assert _append(http, content="Changed under the same key.") == (
        409,
        {"detail": "idempotency_conflict"},
    )
    assert _append(http, key="append-2", content="Stale base.") == (
        409,
        {"detail": "version_conflict"},
    )
    new = http.client.get(f"{FINANCE}/{expected_id}", headers=http.finance)
    assert new.status_code == 200
    assert new.json()["content"] == "Fictional HTTP revision two."
    assert new.json()["source"] == "agent:agent-finance"
    old = http.client.get(FINANCE_V1, headers=http.finance)
    assert old.json()["content"] == "Fictional private revision one."
    assert row_count(http.case.engine, schema.versions) == 4
    assert row_count(http.case.engine, schema.write_requests) == 1
    results = [row["result"] for row in recorded_attempts(http.case.engine)]
    assert sorted(results) == [
        "idempotency_conflict",
        "read",
        "read",
        "version_conflict",
        "written",
        "written",
    ]


def test_idempotency_keys_are_scoped_to_the_agent(http: HttpCase) -> None:
    """Another company's agent reusing a key cannot replay Alpha's result."""
    assert _append(http)[0] == 201
    response = http.client.post(
        "/memory/projects/finance/documents/beta-plan/versions",
        headers={**http.beta, "Idempotency-Key": "append-1"},
        json={
            "base_version_id": "beta-plan-v1",
            "expected_number": 1,
            "content": "Fictional HTTP revision two.",
        },
    )
    assert response.status_code == 201
    assert response.json()["version_id"] == derive_version_id("beta", "beta-plan", 2)
    assert row_count(http.case.engine, schema.write_requests) == 2


@pytest.mark.parametrize(
    "headers",
    [{}, {"Idempotency-Key": ""}, {"Idempotency-Key": "../escape"}],
)
def test_append_requires_a_valid_idempotency_key(
    http: HttpCase, headers: dict[str, str]
) -> None:
    """Writes without a usable retry key are rejected before storage changes."""
    response = http.client.post(
        FINANCE,
        headers={**http.finance, **headers},
        json={
            "base_version_id": "finance-policy-v1",
            "expected_number": 1,
            "content": "No key.",
        },
    )
    assert response.status_code == 422
    assert row_count(http.case.engine, schema.versions) == 3
    assert row_count(http.case.engine, schema.audit_events) == 0


@pytest.mark.parametrize("operation", ["read", "write"])
def test_audit_failure_returns_503_without_content(
    http: HttpCase, operation: str
) -> None:
    """Mandatory audit failure fails closed: no text is returned, nothing is kept."""
    with http.case.engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE audit_events ADD CONSTRAINT test_audit_failure "
                "CHECK (false) NOT VALID"
            )
        )
    try:
        if operation == "read":
            response = http.client.get(FINANCE_V1, headers=http.finance)
        else:
            response = http.client.post(
                FINANCE,
                headers={**http.finance, "Idempotency-Key": "audit-1"},
                json={
                    "base_version_id": "finance-policy-v1",
                    "expected_number": 1,
                    "content": "Must roll back.",
                },
            )
    finally:
        with http.case.engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE audit_events DROP CONSTRAINT test_audit_failure")
            )
    assert response.status_code == 503
    assert response.json() == {"detail": "memory storage unavailable"}
    assert "Fictional" not in response.text
    assert row_count(http.case.engine, schema.versions) == 3
    assert row_count(http.case.engine, schema.write_requests) == 0
    assert row_count(http.case.engine, schema.audit_events) == 0


class RevokingAuthenticator(CredentialAuthenticator):
    """Authenticate normally, then revoke the credential before the operation."""

    def authenticate(self, token: object, *, at: datetime) -> Principal | None:
        """Simulate an administrator revoking access right after authentication."""
        principal = super().authenticate(token, at=at)
        with self.engine.begin() as connection:
            connection.execute(
                select(schema.companies)
                .where(schema.companies.c.id == "alpha")
                .with_for_update()
            )
            connection.execute(
                schema.agent_credentials.update()
                .where(schema.agent_credentials.c.id == "cred-finance")
                .values(active=False)
            )
        return principal


def test_revocation_after_authentication_is_enforced_in_the_operation(
    http: HttpCase,
) -> None:
    """The locked transaction rechecks the credential that authentication returned."""
    services = http.client.app.state.memory  # type: ignore[attr-defined]
    http.client.app.state.memory = type(services)(  # type: ignore[attr-defined]
        store=services.store,
        authenticator=RevokingAuthenticator(http.case.engine),
        clock=services.clock,
    )
    response = http.client.get(FINANCE_V1, headers=http.finance)
    assert response.status_code == 404
    assert "Fictional" not in response.text
    attempt = _only_attempt(http)
    assert attempt["reason"] == "credential_unavailable"
    assert attempt["credential_id"] == "cred-finance"
