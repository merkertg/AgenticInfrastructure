"""Verify the real migration history on the guarded disposable PostgreSQL database."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text

from app.persistence.database import migrate

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TABLES = {
    "companies",
    "projects",
    "employees",
    "agents",
    "delegations",
    "documents",
    "versions",
    "grants",
    "audit_events",
    "write_requests",
    "alembic_version",
}


def test_migration_creates_expected_tables(engine: Engine) -> None:
    """A clean upgrade installs all persistence tables and its revision marker."""
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES
    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert revision


def test_repeated_upgrade_is_safe(engine: Engine, postgres_url: str) -> None:
    """Applying an already-current migration does not recreate or duplicate tables."""
    before = set(inspect(engine).get_table_names())
    migrate(postgres_url)
    assert set(inspect(engine).get_table_names()) == before == EXPECTED_TABLES


def test_downgrade_then_upgrade_reconstructs_schema(
    engine: Engine, postgres_url: str
) -> None:
    """The initial revision can be reversed and reapplied in a disposable database."""
    configuration = Config(str(ROOT / "alembic.ini"))
    configuration.set_main_option("script_location", str(ROOT / "migrations"))
    try:
        with engine.begin() as connection:
            configuration.attributes["connection"] = connection
            command.downgrade(configuration, "base")
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    finally:
        migrate(postgres_url)
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES
