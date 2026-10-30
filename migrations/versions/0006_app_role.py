"""Grant the memory service a least-privilege group role.

The HTTP service reads policy objects and appends versions and history; it
never changes rights, documents, credentials, sources or existing history.
`UPDATE (name)` on companies exists only because `SELECT ... FOR UPDATE` needs
an update privilege; a trigger rejects every actual update by that role.
Roles are cluster-wide: downgrade revokes this database's privileges and keeps
the role, which other databases of the cluster may still use.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_app_role"
down_revision: str | None = "0005_source_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "agentic_memory_app"
READ_ONLY = (
    "projects",
    "employees",
    "agents",
    "agent_credentials",
    "delegations",
    "documents",
    "grants",
    "sources",
    "source_documents",
    "alembic_version",
)
READ_APPEND = ("versions", "write_requests")
APPEND_ONLY = (
    "audit_events",
    "context_requests",
    "context_items",
    "agent_runs",
    "agent_run_searches",
    "agent_run_citations",
)
ALL_TABLES = (
    "companies",
    *READ_ONLY,
    *READ_APPEND,
    *APPEND_ONLY,
    "source_accounts",
    "source_sync_runs",
)


def upgrade() -> None:
    """Create the group role if needed and grant exactly what the service uses."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN;
            END IF;
        END
        $$
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT ON {', '.join(READ_ONLY)} TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON {', '.join(READ_APPEND)} TO {APP_ROLE}")
    op.execute(f"GRANT INSERT ON {', '.join(APPEND_ONLY)} TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, UPDATE (name) ON companies TO {APP_ROLE}")
    op.execute(
        f"""
        CREATE FUNCTION reject_memory_app_company_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_roles AS r
                WHERE r.rolname = current_user
                  AND NOT r.rolsuper
                  AND pg_has_role(current_user, '{APP_ROLE}', 'MEMBER')
            ) THEN
                RAISE EXCEPTION 'the memory service role cannot modify companies'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER companies_reject_memory_app_update BEFORE UPDATE ON companies "
        "FOR EACH ROW EXECUTE FUNCTION reject_memory_app_company_update()"
    )


def downgrade() -> None:
    """Remove the guard and this database's grants; keep the cluster-wide role."""
    op.execute("DROP TRIGGER companies_reject_memory_app_update ON companies")
    op.execute("DROP FUNCTION reject_memory_app_company_update()")
    op.execute(f"REVOKE ALL ON {', '.join(ALL_TABLES)} FROM {APP_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}")
