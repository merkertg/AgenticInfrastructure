"""Add hashed agent bearer credentials and record the credential used in audit.

This revision records its schema explicitly and does not import live tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_agent_credentials"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create credential storage bound to agent registrations and extend audit."""
    op.create_table(
        "agent_credentials",
        sa.Column("company_id", sa.String(length=128), nullable=False),
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("agent_id", sa.String(length=128), nullable=False),
        sa.Column("employee_id", sa.String(length=128), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.CheckConstraint(
            "token_hash ~ '^[a-f0-9]{64}$'",
            name=op.f("ck_agent_credentials_sha256_token_hash"),
        ),
        sa.CheckConstraint(
            "expires_at > issued_at",
            name=op.f("ck_agent_credentials_positive_lifetime"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "agent_id", "employee_id"],
            ["agents.company_id", "agents.id", "agents.employee_id"],
            name=op.f("fk_agent_credentials_company_id_agents"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_agent_credentials_company_id_companies"),
        ),
        sa.PrimaryKeyConstraint("company_id", "id", name=op.f("pk_agent_credentials")),
        sa.UniqueConstraint("token_hash", name="uq_agent_credentials_token_hash"),
    )
    op.add_column(
        "audit_events",
        sa.Column("credential_id", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    """Remove credential storage and the audit credential reference."""
    op.drop_column("audit_events", "credential_id")
    op.drop_table("agent_credentials")
