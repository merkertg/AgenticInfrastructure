"""Record agent runs, the searches they made and the passages their answers cite.

This revision records its schema explicitly and does not import live tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_agent_runs"
down_revision: str | None = "0003_context_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create metadata-only history for agent runs, their searches and citations."""
    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("employee_id", sa.String(length=128), nullable=False),
        sa.Column("agent_id", sa.String(length=128), nullable=False),
        sa.Column("credential_id", sa.String(length=128), nullable=True),
        sa.Column("question_sha256", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("cited_items", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "allowed = (reason = 'allowed')",
            name=op.f("ck_agent_runs_consistent_authorization"),
        ),
        sa.CheckConstraint(
            "outcome IN ('answered', 'unanswered', 'denied', 'evidence_revoked', "
            "'provider_error', 'invalid_turn', 'invalid_answer', 'invalid_citation', "
            "'tool_call_limit', 'step_limit', 'time_limit', 'transcript_limit')",
            name=op.f("ck_agent_runs_supported_outcome"),
        ),
        sa.CheckConstraint(
            "allowed = (outcome NOT IN ('denied', 'evidence_revoked'))",
            name=op.f("ck_agent_runs_consistent_outcome"),
        ),
        sa.CheckConstraint(
            "(outcome = 'answered') = (cited_items > 0)",
            name=op.f("ck_agent_runs_answered_with_citations"),
        ),
        sa.CheckConstraint(
            "question_sha256 ~ '^[a-f0-9]{64}$'",
            name=op.f("ck_agent_runs_sha256_question"),
        ),
        sa.CheckConstraint(
            "steps BETWEEN 0 AND 8", name=op.f("ck_agent_runs_steps_range")
        ),
        sa.CheckConstraint(
            "tool_calls BETWEEN 0 AND 10", name=op.f("ck_agent_runs_tool_calls_range")
        ),
        sa.CheckConstraint(
            "cited_items BETWEEN 0 AND 10",
            name=op.f("ck_agent_runs_cited_items_range"),
        ),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_agent_runs")),
    )
    op.create_table(
        "agent_run_searches",
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("context_request_id", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "position BETWEEN 1 AND 10",
            name=op.f("ck_agent_run_searches_position_range"),
        ),
        sa.ForeignKeyConstraint(
            ["context_request_id"],
            ["context_requests.request_id"],
            name=op.f("fk_agent_run_searches_context_request_id_context_requests"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.run_id"],
            name=op.f("fk_agent_run_searches_run_id_agent_runs"),
        ),
        sa.PrimaryKeyConstraint(
            "run_id", "position", name=op.f("pk_agent_run_searches")
        ),
        sa.UniqueConstraint(
            "context_request_id",
            name=op.f("uq_agent_run_searches_context_request_id"),
        ),
    )
    op.create_table(
        "agent_run_citations",
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("document_id", sa.String(length=128), nullable=False),
        sa.Column("version_id", sa.String(length=128), nullable=False),
        sa.Column("fragment_ordinal", sa.Integer(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "position BETWEEN 1 AND 10",
            name=op.f("ck_agent_run_citations_position_range"),
        ),
        sa.CheckConstraint(
            "start_offset >= 0 AND end_offset > start_offset",
            name=op.f("ck_agent_run_citations_valid_span"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "document_id", "version_id"],
            ["versions.company_id", "versions.document_id", "versions.id"],
            name=op.f("fk_agent_run_citations_company_id_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.run_id"],
            name=op.f("fk_agent_run_citations_run_id_agent_runs"),
        ),
        sa.PrimaryKeyConstraint(
            "run_id", "position", name=op.f("pk_agent_run_citations")
        ),
    )


def downgrade() -> None:
    """Remove agent run history tables."""
    op.drop_table("agent_run_citations")
    op.drop_table("agent_run_searches")
    op.drop_table("agent_runs")
