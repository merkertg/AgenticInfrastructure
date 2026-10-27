"""Record context-search requests and the fragment references they returned.

This revision records its schema explicitly and does not import live tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_context_history"
down_revision: str | None = "0002_agent_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create metadata-only history for context searches and returned references."""
    op.create_table(
        "context_requests",
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("employee_id", sa.String(length=128), nullable=False),
        sa.Column("agent_id", sa.String(length=128), nullable=False),
        sa.Column("credential_id", sa.String(length=128), nullable=True),
        sa.Column("query_sha256", sa.String(length=64), nullable=False),
        sa.Column("query_terms", sa.Integer(), nullable=False),
        sa.Column("max_items", sa.Integer(), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("returned_items", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "allowed = (reason = 'allowed')",
            name=op.f("ck_context_requests_consistent_authorization"),
        ),
        sa.CheckConstraint(
            "result IN ('found', 'no_authorized_evidence', 'denied')",
            name=op.f("ck_context_requests_supported_result"),
        ),
        sa.CheckConstraint(
            "query_sha256 ~ '^[a-f0-9]{64}$'",
            name=op.f("ck_context_requests_sha256_query"),
        ),
        sa.CheckConstraint(
            "returned_items BETWEEN 0 AND 10",
            name=op.f("ck_context_requests_returned_items_range"),
        ),
        sa.PrimaryKeyConstraint("request_id", name=op.f("pk_context_requests")),
    )
    op.create_table(
        "context_items",
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("document_id", sa.String(length=128), nullable=False),
        sa.Column("version_id", sa.String(length=128), nullable=False),
        sa.Column("fragment_ordinal", sa.Integer(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "start_offset >= 0 AND end_offset > start_offset",
            name=op.f("ck_context_items_valid_span"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "document_id", "version_id"],
            ["versions.company_id", "versions.document_id", "versions.id"],
            name=op.f("fk_context_items_company_id_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["context_requests.request_id"],
            name=op.f("fk_context_items_request_id_context_requests"),
        ),
        sa.PrimaryKeyConstraint(
            "request_id", "position", name=op.f("pk_context_items")
        ),
    )


def downgrade() -> None:
    """Remove context-search history tables."""
    op.drop_table("context_items")
    op.drop_table("context_requests")
