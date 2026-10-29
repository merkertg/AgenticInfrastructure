"""Verify the readiness probe against the migrated disposable PostgreSQL database."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.config import Settings
from app.persistence.database import SCHEMA_HEAD

pytestmark = pytest.mark.postgres


@pytest.fixture
def ready_client(engine: Engine) -> Iterator[TestClient]:
    """Serve the application over the test database."""
    from app.main import create_app

    with TestClient(create_app(Settings(), engine=engine)) as client:
        yield client


def test_migrated_database_is_ready(ready_client: TestClient) -> None:
    """A reachable database at the expected revision is ready."""
    response = ready_client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok", "schema": "current"}


def test_outdated_or_missing_revision_is_not_ready(
    engine: Engine, ready_client: TestClient
) -> None:
    """A schema at another revision, or without one, must be migrated first."""
    try:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num = '0004_agent_runs'")
            )
        outdated = ready_client.get("/ready")
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM alembic_version"))
        missing = ready_client.get("/ready")
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM alembic_version"))
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
                {"head": SCHEMA_HEAD},
            )
    expected = {"status": "not_ready", "database": "ok", "schema": "outdated"}
    assert (outdated.status_code, outdated.json()) == (503, expected)
    assert (missing.status_code, missing.json()) == (503, expected)
    assert ready_client.get("/ready").status_code == 200
