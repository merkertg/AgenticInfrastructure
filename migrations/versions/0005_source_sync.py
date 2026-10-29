"""Register one read-only source, its account mapping, synced documents and runs.

This revision records its schema explicitly and does not import live tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_source_sync"
down_revision: str | None = "0004_agent_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create source settings, account mapping, document state and run history."""
    op.create_table(
        "sources",
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=256), nullable=False),
        sa.Column("space", sa.String(length=64), nullable=False),
        sa.Column("document_prefix", sa.String(length=32), nullable=False),
        sa.Column("max_age_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind = 'handbook-http-v1'", name=op.f("ck_sources_supported_kind")
        ),
        sa.CheckConstraint(
            "max_age_seconds BETWEEN 60 AND 86400",
            name=op.f("ck_sources_max_age_range"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "project_id"],
            ["projects.company_id", "projects.id"],
            name=op.f("fk_sources_company_id_projects"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_sources_company_id_companies"),
        ),
        sa.PrimaryKeyConstraint("company_id", "id", name=op.f("pk_sources")),
        sa.UniqueConstraint("company_id", "id", "project_id", name="uq_sources_scope"),
        sa.UniqueConstraint("company_id", "document_prefix", name="uq_sources_prefix"),
    )
    op.create_table(
        "source_accounts",
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("principal", sa.String(length=128), nullable=False),
        sa.Column("employee_id", sa.String(length=128), nullable=False),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "employee_id"],
            ["employees.company_id", "employees.id"],
            name=op.f("fk_source_accounts_company_id_employees"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "source_id"],
            ["sources.company_id", "sources.id"],
            name=op.f("fk_source_accounts_company_id_sources"),
        ),
        sa.PrimaryKeyConstraint(
            "company_id", "source_id", "principal", name=op.f("pk_source_accounts")
        ),
    )
    op.create_table(
        "source_documents",
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("document_id", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.String(length=64), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("withheld_reason", sa.String(length=32), nullable=True),
        sa.CheckConstraint(
            "state IN ('active', 'deleted', 'withheld')",
            name=op.f("ck_source_documents_supported_state"),
        ),
        sa.CheckConstraint(
            "(state = 'withheld') = (withheld_reason IS NOT NULL)",
            name=op.f("ck_source_documents_consistent_withheld"),
        ),
        sa.CheckConstraint(
            "(revision IS NULL) = (content_sha256 IS NULL)",
            name=op.f("ck_source_documents_consistent_import"),
        ),
        sa.CheckConstraint(
            "state <> 'active' OR revision IS NOT NULL",
            name=op.f("ck_source_documents_active_with_content"),
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[a-f0-9]{64}$'",
            name=op.f("ck_source_documents_sha256_content"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "document_id", "project_id"],
            ["documents.company_id", "documents.id", "documents.project_id"],
            name=op.f("fk_source_documents_company_id_documents"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "source_id", "project_id"],
            ["sources.company_id", "sources.id", "sources.project_id"],
            name=op.f("fk_source_documents_company_id_sources"),
        ),
        sa.PrimaryKeyConstraint(
            "company_id",
            "source_id",
            "external_id",
            name=op.f("pk_source_documents"),
        ),
        sa.UniqueConstraint(
            "company_id", "document_id", name="uq_source_documents_document"
        ),
    )
    op.create_table(
        "source_sync_runs",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("failure", sa.String(length=32), nullable=True),
        sa.Column("listed", sa.Integer(), nullable=False),
        sa.Column("fetched", sa.Integer(), nullable=False),
        sa.Column("created", sa.Integer(), nullable=False),
        sa.Column("updated", sa.Integer(), nullable=False),
        sa.Column("unchanged", sa.Integer(), nullable=False),
        sa.Column("restored", sa.Integer(), nullable=False),
        sa.Column("deleted", sa.Integer(), nullable=False),
        sa.Column("withheld", sa.Integer(), nullable=False),
        sa.Column("unmapped_readers", sa.Integer(), nullable=False),
        sa.Column("grants_added", sa.Integer(), nullable=False),
        sa.Column("grants_revoked", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('succeeded', 'failed')",
            name=op.f("ck_source_sync_runs_supported_outcome"),
        ),
        sa.CheckConstraint(
            "(outcome = 'succeeded') = (failure IS NULL)",
            name=op.f("ck_source_sync_runs_consistent_failure"),
        ),
        sa.CheckConstraint(
            "finished_at >= started_at",
            name=op.f("ck_source_sync_runs_ordered_times"),
        ),
        sa.CheckConstraint(
            "LEAST(listed, fetched, created, updated, unchanged, restored, deleted, "
            "withheld, unmapped_readers, grants_added, grants_revoked) >= 0",
            name=op.f("ck_source_sync_runs_non_negative_counts"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "source_id"],
            ["sources.company_id", "sources.id"],
            name=op.f("fk_source_sync_runs_company_id_sources"),
        ),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_source_sync_runs")),
    )


def downgrade() -> None:
    """Keep mirrors inaccessible before removing their freshness and write gates.

    Versions stay for historical references. Re-upgrading cannot reconstruct
    their source ownership, so restoring a mirror requires trusted setup.
    """
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "SELECT id FROM companies "
            "WHERE id IN (SELECT company_id FROM source_documents) "
            "ORDER BY id FOR UPDATE"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE documents SET active = false FROM source_documents "
            "WHERE documents.company_id = source_documents.company_id "
            "AND documents.id = source_documents.document_id"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE grants SET active = false FROM source_documents "
            "WHERE grants.company_id = source_documents.company_id "
            "AND grants.document_id = source_documents.document_id"
        )
    )
    op.drop_table("source_sync_runs")
    op.drop_table("source_documents")
    op.drop_table("source_accounts")
    op.drop_table("sources")
