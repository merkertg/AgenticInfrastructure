"""Verify database relationships independently of the application policy code."""

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from app.persistence import schema
from tests.persistence.conftest import PersistenceCase

pytestmark = pytest.mark.postgres


def test_project_requires_a_real_company(engine: Engine) -> None:
    """The database cannot save a project for a nonexistent company."""
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            schema.projects.insert(),
            {
                "company_id": "unknown",
                "id": "finance",
                "name": "Finance",
                "active": True,
            },
        )


def test_agent_cannot_bind_employee_of_another_company(case: PersistenceCase) -> None:
    """Matching employee IDs in a foreign company cannot satisfy the owner relation."""
    with case.engine.begin() as connection:
        connection.execute(
            schema.companies.insert(), {"id": "beta", "name": "Beta", "active": True}
        )
    with pytest.raises(IntegrityError), case.engine.begin() as connection:
        connection.execute(
            schema.agents.insert(),
            {
                "company_id": "beta",
                "id": "beta-agent",
                "employee_id": "employee-finance",
                "name": "Foreign assistant",
                "active": True,
            },
        )


def test_delegation_requires_agent_and_employee_pair(case: PersistenceCase) -> None:
    """An agent registration cannot be delegated on behalf of a different employee."""
    with case.engine.begin() as connection:
        connection.execute(
            schema.employees.insert(),
            {
                "company_id": "alpha",
                "id": "other-employee",
                "name": "Other fictional user",
                "active": True,
            },
        )
    with pytest.raises(IntegrityError), case.engine.begin() as connection:
        connection.execute(
            schema.delegations.insert(),
            {
                "company_id": "alpha",
                "id": "mismatched-delegation",
                "project_id": "finance",
                "employee_id": "other-employee",
                "agent_id": "agent-finance",
                "actions": ["read"],
                "active": True,
            },
        )


def test_grant_project_must_match_its_document(case: PersistenceCase) -> None:
    """A grant cannot attach a finance document to a different project."""
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
    with pytest.raises(IntegrityError), case.engine.begin() as connection:
        connection.execute(
            schema.grants.insert(),
            {
                "company_id": "alpha",
                "id": "wrong-project-grant",
                "project_id": "engineering",
                "employee_id": "employee-finance",
                "document_id": "finance-policy",
                "actions": ["read"],
                "active": True,
            },
        )


def test_document_version_sequence_is_unique(case: PersistenceCase) -> None:
    """A different revision ID cannot duplicate the same document version number."""
    with pytest.raises(IntegrityError), case.engine.begin() as connection:
        connection.execute(
            schema.versions.insert(),
            {
                "company_id": "alpha",
                "id": "duplicate-number",
                "document_id": "finance-policy",
                "number": 1,
                "source": "fixture:other",
                "content": "Other fictional text.",
                "active": True,
            },
        )


def test_version_cannot_reference_another_company_document(
    case: PersistenceCase,
) -> None:
    """A document ID cannot be joined across company boundaries by raw SQL."""
    with case.engine.begin() as connection:
        connection.execute(
            schema.companies.insert(), {"id": "beta", "name": "Beta", "active": True}
        )
    with pytest.raises(IntegrityError), case.engine.begin() as connection:
        connection.execute(
            schema.versions.insert(),
            {
                "company_id": "beta",
                "id": "foreign-revision",
                "document_id": "finance-policy",
                "number": 1,
                "source": "fixture:foreign",
                "content": "Foreign fictional text.",
                "active": True,
            },
        )
