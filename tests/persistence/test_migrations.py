"""Verify the real migration history on the guarded disposable PostgreSQL database."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, select, text

from app.application.source_sync import Snapshot
from app.domain.models import ReasonCode
from app.domain.source import ListedDocument, SourceSettings
from app.domain.versions import derive_version_id
from app.persistence import schema
from app.persistence.database import migrate
from app.persistence.sources import apply_snapshot, register_source
from tests.persistence.conftest import TIMESTAMP, PersistenceCase

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TABLES = {
    "companies",
    "projects",
    "employees",
    "agents",
    "agent_credentials",
    "delegations",
    "documents",
    "versions",
    "grants",
    "audit_events",
    "write_requests",
    "context_requests",
    "context_items",
    "agent_runs",
    "agent_run_searches",
    "agent_run_citations",
    "sources",
    "source_accounts",
    "source_documents",
    "source_sync_runs",
    "alembic_version",
}
CONTEXT_TABLES = {"context_requests", "context_items"}
RUN_TABLES = {"agent_runs", "agent_run_searches", "agent_run_citations"}
SOURCE_TABLES = {"sources", "source_accounts", "source_documents", "source_sync_runs"}


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


def test_credential_revision_downgrades_to_initial_schema(
    engine: Engine, postgres_url: str
) -> None:
    """Reverting Stage 04 removes credentials and the audit credential column only."""
    configuration = Config(str(ROOT / "alembic.ini"))
    configuration.set_main_option("script_location", str(ROOT / "migrations"))
    try:
        with engine.begin() as connection:
            configuration.attributes["connection"] = connection
            command.downgrade(configuration, "0001_initial")
        inspector = inspect(engine)
        assert set(inspector.get_table_names()) == EXPECTED_TABLES - {
            "agent_credentials",
            *CONTEXT_TABLES,
            *RUN_TABLES,
            *SOURCE_TABLES,
        }
        audit_columns = {
            column["name"] for column in inspector.get_columns("audit_events")
        }
        assert "credential_id" not in audit_columns
    finally:
        migrate(postgres_url)
    with engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert revision == "0005_source_sync"
    audit_columns = {
        column["name"] for column in inspect(engine).get_columns("audit_events")
    }
    assert "credential_id" in audit_columns


def test_context_revision_downgrades_to_credential_schema(
    engine: Engine, postgres_url: str
) -> None:
    """Reverting Stage 05 removes only the context-history tables."""
    configuration = Config(str(ROOT / "alembic.ini"))
    configuration.set_main_option("script_location", str(ROOT / "migrations"))
    try:
        with engine.begin() as connection:
            configuration.attributes["connection"] = connection
            command.downgrade(configuration, "0002_agent_credentials")
        assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES - (
            CONTEXT_TABLES | RUN_TABLES | SOURCE_TABLES
        )
    finally:
        migrate(postgres_url)
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES


def test_run_revision_downgrades_to_context_schema(
    engine: Engine, postgres_url: str
) -> None:
    """Reverting Stage 06 removes only the agent run history tables."""
    configuration = Config(str(ROOT / "alembic.ini"))
    configuration.set_main_option("script_location", str(ROOT / "migrations"))
    try:
        with engine.begin() as connection:
            configuration.attributes["connection"] = connection
            command.downgrade(configuration, "0003_context_history")
        assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES - (
            RUN_TABLES | SOURCE_TABLES
        )
    finally:
        migrate(postgres_url)
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES


def test_source_revision_downgrades_to_run_schema(
    engine: Engine, postgres_url: str
) -> None:
    """Reverting Stage 07 removes only the source tables."""
    configuration = Config(str(ROOT / "alembic.ini"))
    configuration.set_main_option("script_location", str(ROOT / "migrations"))
    try:
        with engine.begin() as connection:
            configuration.attributes["connection"] = connection
            command.downgrade(configuration, "0004_agent_runs")
        assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES - SOURCE_TABLES
    finally:
        migrate(postgres_url)
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES


def test_source_downgrade_keeps_mirrors_unavailable(
    case: PersistenceCase, postgres_url: str
) -> None:
    """Removing source gates cannot restore expired access or local write grants."""
    settings = SourceSettings(
        id="handbook",
        company_id="alpha",
        project_id="finance",
        base_url="https://handbook.alpha.example",
        space="alpha-handbook",
        document_prefix="hb-",
        max_age_seconds=60,
    )
    register_source(case.engine, settings, {"u-fin": "employee-finance"})
    started_at = TIMESTAMP - timedelta(minutes=10)
    apply_snapshot(
        case.engine,
        settings,
        Snapshot(
            documents=(
                ListedDocument(
                    external_id="policy",
                    title="Fictional mirrored policy",
                    revision="r1",
                    principals=frozenset({"u-fin"}),
                ),
            ),
            contents={"policy": "Fictional source text."},
        ),
        run_id="downgrade-source-run",
        started_at=started_at,
        clock=lambda: started_at,
    )
    request = replace(
        case.request,
        document_id="hb-policy",
        version_id=derive_version_id("alpha", "hb-policy", 1),
    )
    assert (
        case.store.read_version(request, occurred_at=TIMESTAMP).decision.reason
        is ReasonCode.SOURCE_UNCONFIRMED
    )
    with case.engine.begin() as connection:
        connection.execute(
            schema.grants.insert(),
            {
                "company_id": "alpha",
                "id": "manual-source-write",
                "project_id": "finance",
                "employee_id": "employee-finance",
                "document_id": "hb-policy",
                "actions": ["read", "write"],
            },
        )
    configuration = Config(str(ROOT / "alembic.ini"))
    configuration.set_main_option("script_location", str(ROOT / "migrations"))
    try:
        with case.engine.begin() as connection:
            configuration.attributes["connection"] = connection
            command.downgrade(configuration, "0004_agent_runs")
        with case.engine.connect() as connection:
            documents = dict(
                connection.execute(
                    select(schema.documents.c.id, schema.documents.c.active)
                ).all()
            )
            grants = dict(
                connection.execute(
                    select(schema.grants.c.id, schema.grants.c.active)
                ).all()
            )
        assert documents == {"finance-policy": True, "hb-policy": False}
        assert grants.pop("grant-1") is True
        assert len(grants) == 2 and not any(grants.values())
    finally:
        migrate(postgres_url)
    assert (
        case.store.read_version(
            replace(request, request_id="read-after-downgrade"), occurred_at=TIMESTAMP
        ).decision.reason
        is ReasonCode.DOCUMENT_UNAVAILABLE
    )
