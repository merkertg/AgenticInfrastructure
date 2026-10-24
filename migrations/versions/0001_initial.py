"""Create the initial scoped memory, audit and idempotency tables.

This revision records its schema explicitly and does not import live tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create scoped entities, version history and audit metadata tables."""
    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("employee_id", sa.String(length=128), nullable=False),
        sa.Column("agent_id", sa.String(length=128), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=5), nullable=False),
        sa.Column("document_id", sa.String(length=128), nullable=False),
        sa.Column("version_id", sa.String(length=128), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("result_version_id", sa.String(length=128), nullable=True),
        sa.Column("result_version_number", sa.Integer(), nullable=True),
        sa.Column(
            "replayed", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.CheckConstraint(
            "action IN ('read', 'write')", name=op.f("ck_audit_events_supported_action")
        ),
        sa.CheckConstraint(
            "allowed = (reason = 'allowed')",
            name=op.f("ck_audit_events_consistent_authorization"),
        ),
        sa.CheckConstraint(
            "result IN ('read', 'written', 'denied', 'version_conflict', "
            "'idempotency_conflict')",
            name=op.f("ck_audit_events_supported_result"),
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_audit_events")),
    )
    op.create_table(
        "companies",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_companies")),
    )
    op.create_table(
        "employees",
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_employees_company_id_companies"),
        ),
        sa.PrimaryKeyConstraint("company_id", "id", name=op.f("pk_employees")),
    )
    op.create_table(
        "projects",
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_projects_company_id_companies"),
        ),
        sa.PrimaryKeyConstraint("company_id", "id", name=op.f("pk_projects")),
    )
    op.create_table(
        "agents",
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("employee_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "employee_id"],
            ["employees.company_id", "employees.id"],
            name=op.f("fk_agents_company_id_employees"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_agents_company_id_companies"),
        ),
        sa.PrimaryKeyConstraint("company_id", "id", name=op.f("pk_agents")),
        sa.UniqueConstraint(
            "company_id", "id", "employee_id", name="uq_agents_binding"
        ),
    )
    op.create_table(
        "documents",
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "project_id"],
            ["projects.company_id", "projects.id"],
            name=op.f("fk_documents_company_id_projects"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_documents_company_id_companies"),
        ),
        sa.PrimaryKeyConstraint("company_id", "id", name=op.f("pk_documents")),
        sa.UniqueConstraint(
            "company_id", "id", "project_id", name="uq_documents_scope"
        ),
    )
    op.create_table(
        "delegations",
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("employee_id", sa.String(length=128), nullable=False),
        sa.Column("agent_id", sa.String(length=128), nullable=False),
        sa.Column("actions", postgresql.JSONB(), nullable=False),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.CheckConstraint(
            "jsonb_typeof(actions) = 'array' "
            'AND actions <@ \'["read", "write"]\'::jsonb',
            name=op.f("ck_delegations_supported_actions"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "agent_id", "employee_id"],
            ["agents.company_id", "agents.id", "agents.employee_id"],
            name=op.f("fk_delegations_company_id_agents"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "project_id"],
            ["projects.company_id", "projects.id"],
            name=op.f("fk_delegations_company_id_projects"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_delegations_company_id_companies"),
        ),
        sa.PrimaryKeyConstraint("company_id", "id", name=op.f("pk_delegations")),
        sa.UniqueConstraint(
            "company_id",
            "project_id",
            "employee_id",
            "agent_id",
            name="uq_delegations_scope",
        ),
    )
    op.create_table(
        "grants",
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("employee_id", sa.String(length=128), nullable=False),
        sa.Column("document_id", sa.String(length=128), nullable=False),
        sa.Column("actions", postgresql.JSONB(), nullable=False),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.CheckConstraint(
            "jsonb_typeof(actions) = 'array' "
            'AND actions <@ \'["read", "write"]\'::jsonb',
            name=op.f("ck_grants_supported_actions"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "document_id", "project_id"],
            ["documents.company_id", "documents.id", "documents.project_id"],
            name=op.f("fk_grants_company_id_documents"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "employee_id"],
            ["employees.company_id", "employees.id"],
            name=op.f("fk_grants_company_id_employees"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_grants_company_id_companies"),
        ),
        sa.PrimaryKeyConstraint("company_id", "id", name=op.f("pk_grants")),
    )
    op.create_table(
        "versions",
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("document_id", sa.String(length=128), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=256), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.CheckConstraint(
            "char_length(content) BETWEEN 1 AND 20000",
            name=op.f("ck_versions_content_size"),
        ),
        sa.CheckConstraint(
            "number BETWEEN 1 AND 1000000", name=op.f("ck_versions_number_range")
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "document_id"],
            ["documents.company_id", "documents.id"],
            name=op.f("fk_versions_company_id_documents"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_versions_company_id_companies"),
        ),
        sa.PrimaryKeyConstraint("company_id", "id", name=op.f("pk_versions")),
        sa.UniqueConstraint(
            "company_id", "document_id", "id", name="uq_versions_document"
        ),
        sa.UniqueConstraint(
            "company_id", "document_id", "number", name="uq_versions_number"
        ),
    )
    op.create_table(
        "write_requests",
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("employee_id", sa.String(length=128), nullable=False),
        sa.Column("agent_id", sa.String(length=128), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.String(length=128), nullable=False),
        sa.Column("result_version_id", sa.String(length=128), nullable=False),
        sa.Column("result_version_number", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "fingerprint ~ '^[a-f0-9]{64}$'",
            name=op.f("ck_write_requests_sha256_fingerprint"),
        ),
        sa.CheckConstraint(
            "result_version_number BETWEEN 1 AND 1000000",
            name=op.f("ck_write_requests_number_range"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "document_id", "result_version_id"],
            ["versions.company_id", "versions.document_id", "versions.id"],
            name=op.f("fk_write_requests_company_id_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_write_requests_company_id_companies"),
        ),
        sa.PrimaryKeyConstraint(
            "company_id",
            "employee_id",
            "agent_id",
            "request_id",
            name=op.f("pk_write_requests"),
        ),
    )


def downgrade() -> None:
    """Remove the initial schema in reverse dependency order."""
    op.drop_table("write_requests")
    op.drop_table("versions")
    op.drop_table("grants")
    op.drop_table("delegations")
    op.drop_table("documents")
    op.drop_table("agents")
    op.drop_table("projects")
    op.drop_table("employees")
    op.drop_table("companies")
    op.drop_table("audit_events")
