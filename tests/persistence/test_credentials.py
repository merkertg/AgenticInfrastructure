"""Verify hashed credential storage, authentication and in-transaction rechecks."""

import hashlib
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.application.identity import Principal
from app.application.memory import MemoryOutcome
from app.domain.models import ReasonCode
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


def _issue(case: PersistenceCase, credential_id: str = "cred-finance") -> str:
    """Issue a one-hour fictional credential around the shared test timestamp."""
    return issue_credential(
        case.engine,
        company_id="alpha",
        credential_id=credential_id,
        agent_id="agent-finance",
        issued_at=TIMESTAMP - timedelta(minutes=5),
        expires_at=TIMESTAMP + timedelta(hours=1),
    )


def _set_credential(case: PersistenceCase, **values: object) -> None:
    """Change a credential as trusted administration would, under the company lock."""
    with case.engine.begin() as connection:
        connection.execute(
            select(schema.companies)
            .where(schema.companies.c.id == "alpha")
            .with_for_update()
        )
        connection.execute(schema.agent_credentials.update().values(**values))


def test_issue_stores_only_a_digest_bound_to_the_agent_employee(
    case: PersistenceCase,
) -> None:
    """The plain token is returned once; the row keeps its SHA-256 and binding."""
    token = _issue(case)
    with case.engine.connect() as connection:
        row = connection.execute(select(schema.agent_credentials)).mappings().one()
        dumped = connection.scalar(
            text("SELECT row_to_json(agent_credentials)::text FROM agent_credentials")
        )
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert row["employee_id"] == "employee-finance"
    assert row["active"] is True
    assert token not in str(dumped)


def test_issue_refuses_unknown_agents_and_invalid_lifetimes(
    case: PersistenceCase,
) -> None:
    """Issuance needs a registered agent and an aware, positive lifetime."""
    with pytest.raises(ValueError, match="agent registration"):
        issue_credential(
            case.engine,
            company_id="alpha",
            credential_id="cred-x",
            agent_id="agent-missing",
            issued_at=TIMESTAMP,
            expires_at=TIMESTAMP + timedelta(hours=1),
        )
    with pytest.raises(ValueError, match="later"):
        issue_credential(
            case.engine,
            company_id="alpha",
            credential_id="cred-x",
            agent_id="agent-finance",
            issued_at=TIMESTAMP,
            expires_at=TIMESTAMP,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        issue_credential(
            case.engine,
            company_id="alpha",
            credential_id="cred-x",
            agent_id="agent-finance",
            issued_at=TIMESTAMP.replace(tzinfo=None),
            expires_at=TIMESTAMP + timedelta(hours=1),
        )
    assert row_count(case.engine, schema.agent_credentials) == 0


def test_authentication_returns_the_registered_actor(case: PersistenceCase) -> None:
    """A valid token resolves to the stored company, employee, agent and credential."""
    token = _issue(case)
    assert CredentialAuthenticator(case.engine).authenticate(
        token, at=TIMESTAMP
    ) == Principal(
        company_id="alpha",
        employee_id="employee-finance",
        agent_id="agent-finance",
        credential_id="cred-finance",
    )


def test_unknown_revoked_and_expired_tokens_are_refused(case: PersistenceCase) -> None:
    """Every failure returns None, so callers cannot learn why a token failed."""
    token = _issue(case)
    authenticator = CredentialAuthenticator(case.engine)
    assert authenticator.authenticate(generate_token(), at=TIMESTAMP) is None
    # Expiry is exclusive: a token is refused at its exact expiry instant.
    assert authenticator.authenticate(token, at=TIMESTAMP + timedelta(hours=1)) is None
    _set_credential(case, active=False)
    assert authenticator.authenticate(token, at=TIMESTAMP) is None


def test_credential_must_match_the_agent_registration(case: PersistenceCase) -> None:
    """A credential cannot bind an agent to an employee it is not registered for."""
    with case.engine.begin() as connection:
        connection.execute(
            schema.employees.insert(),
            {"company_id": "alpha", "id": "employee-other", "name": "Other"},
        )
    with pytest.raises(IntegrityError), case.engine.begin() as connection:
        connection.execute(
            schema.agent_credentials.insert(),
            {
                "company_id": "alpha",
                "id": "cred-forged",
                "agent_id": "agent-finance",
                "employee_id": "employee-other",
                "token_hash": "a" * 64,
                "issued_at": TIMESTAMP,
                "expires_at": TIMESTAMP + timedelta(hours=1),
            },
        )
    with pytest.raises(IntegrityError), case.engine.begin() as connection:
        connection.execute(
            schema.agent_credentials.insert(),
            {
                "company_id": "alpha",
                "id": "cred-plain",
                "agent_id": "agent-finance",
                "employee_id": "employee-finance",
                "token_hash": "aik1_not-a-digest",
                "issued_at": TIMESTAMP,
                "expires_at": TIMESTAMP + timedelta(hours=1),
            },
        )


@pytest.mark.parametrize("change", ["revoked", "expired", "other_agent"])
def test_store_rechecks_the_credential_under_the_company_lock(
    case: PersistenceCase, change: str
) -> None:
    """A credential that lapsed after authentication denies and is audited."""
    _issue(case)
    request = case.request
    occurred_at = TIMESTAMP
    if change == "revoked":
        _set_credential(case, active=False)
    elif change == "expired":
        occurred_at = TIMESTAMP + timedelta(hours=2)
    else:
        request = replace(request, agent_id="agent-other")
    result = case.store.read_version(
        request, occurred_at=occurred_at, credential_id="cred-finance"
    )
    assert result.outcome is MemoryOutcome.DENIED
    assert result.version is None
    assert result.decision.reason is ReasonCode.CREDENTIAL_UNAVAILABLE
    attempt = recorded_attempts(case.engine)[0]
    assert attempt["credential_id"] == "cred-finance"
    assert attempt["reason"] == "credential_unavailable"


def test_lapsed_credential_cannot_replay_an_earlier_write(
    case: PersistenceCase,
) -> None:
    """A stored idempotent success is not returned once the credential is revoked."""
    _issue(case)
    first = case.store.write_version(
        case.write_request(),
        new_version=case.new_version,
        expected_number=1,
        occurred_at=TIMESTAMP,
        credential_id="cred-finance",
    )
    assert first.outcome is MemoryOutcome.WRITTEN
    _set_credential(case, active=False)
    retry = case.store.write_version(
        case.write_request(),
        new_version=case.new_version,
        expected_number=1,
        occurred_at=TIMESTAMP,
        credential_id="cred-finance",
    )
    assert retry.outcome is MemoryOutcome.DENIED
    assert retry.decision.reason is ReasonCode.CREDENTIAL_UNAVAILABLE
    assert row_count(case.engine, schema.versions) == 2


def test_trusted_python_calls_record_no_credential(case: PersistenceCase) -> None:
    """Stage 03 callers remain supported and their audit has no credential ID."""
    assert (
        case.store.read_version(case.request, occurred_at=TIMESTAMP).outcome
        is MemoryOutcome.READ
    )
    assert recorded_attempts(case.engine)[0]["credential_id"] is None
