"""Define scoped PostgreSQL tables without importing the HTTP application."""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData(
    naming_convention={
        "pk": "pk_%(table_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
    }
)

companies = Table(
    "companies",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("name", String(256), nullable=False),
    Column("active", Boolean, nullable=False, server_default=text("true")),
)

projects = Table(
    "projects",
    metadata,
    Column("company_id", String(128), ForeignKey("companies.id"), primary_key=True),
    Column("id", String(128), primary_key=True),
    Column("name", String(256), nullable=False),
    Column("active", Boolean, nullable=False, server_default=text("true")),
)

employees = Table(
    "employees",
    metadata,
    Column("company_id", String(128), ForeignKey("companies.id"), primary_key=True),
    Column("id", String(128), primary_key=True),
    Column("name", String(256), nullable=False),
    Column("active", Boolean, nullable=False, server_default=text("true")),
)

agents = Table(
    "agents",
    metadata,
    Column("company_id", String(128), ForeignKey("companies.id"), primary_key=True),
    Column("id", String(128), primary_key=True),
    Column("employee_id", String(128), nullable=False),
    Column("name", String(256), nullable=False),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    ForeignKeyConstraint(
        ["company_id", "employee_id"], ["employees.company_id", "employees.id"]
    ),
    UniqueConstraint("company_id", "id", "employee_id", name="uq_agents_binding"),
)

delegations = Table(
    "delegations",
    metadata,
    Column("company_id", String(128), ForeignKey("companies.id"), primary_key=True),
    Column("id", String(128), primary_key=True),
    Column("project_id", String(128), nullable=False),
    Column("employee_id", String(128), nullable=False),
    Column("agent_id", String(128), nullable=False),
    Column("actions", JSONB, nullable=False),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    ForeignKeyConstraint(
        ["company_id", "project_id"], ["projects.company_id", "projects.id"]
    ),
    ForeignKeyConstraint(
        ["company_id", "agent_id", "employee_id"],
        ["agents.company_id", "agents.id", "agents.employee_id"],
    ),
    UniqueConstraint(
        "company_id",
        "project_id",
        "employee_id",
        "agent_id",
        name="uq_delegations_scope",
    ),
    CheckConstraint(
        "jsonb_typeof(actions) = 'array' AND actions <@ '[\"read\", \"write\"]'::jsonb",
        name="supported_actions",
    ),
)

documents = Table(
    "documents",
    metadata,
    Column("company_id", String(128), ForeignKey("companies.id"), primary_key=True),
    Column("id", String(128), primary_key=True),
    Column("project_id", String(128), nullable=False),
    Column("title", String(256), nullable=False),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    ForeignKeyConstraint(
        ["company_id", "project_id"], ["projects.company_id", "projects.id"]
    ),
    UniqueConstraint("company_id", "id", "project_id", name="uq_documents_scope"),
)

versions = Table(
    "versions",
    metadata,
    Column("company_id", String(128), ForeignKey("companies.id"), primary_key=True),
    Column("id", String(128), primary_key=True),
    Column("document_id", String(128), nullable=False),
    Column("number", Integer, nullable=False),
    Column("source", String(256), nullable=False),
    Column("content", Text, nullable=False),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    ForeignKeyConstraint(
        ["company_id", "document_id"], ["documents.company_id", "documents.id"]
    ),
    UniqueConstraint("company_id", "document_id", "number", name="uq_versions_number"),
    UniqueConstraint("company_id", "document_id", "id", name="uq_versions_document"),
    CheckConstraint("number BETWEEN 1 AND 1000000", name="number_range"),
    CheckConstraint("char_length(content) BETWEEN 1 AND 20000", name="content_size"),
)

grants = Table(
    "grants",
    metadata,
    Column("company_id", String(128), ForeignKey("companies.id"), primary_key=True),
    Column("id", String(128), primary_key=True),
    Column("project_id", String(128), nullable=False),
    Column("employee_id", String(128), nullable=False),
    Column("document_id", String(128), nullable=False),
    Column("actions", JSONB, nullable=False),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    ForeignKeyConstraint(
        ["company_id", "employee_id"], ["employees.company_id", "employees.id"]
    ),
    ForeignKeyConstraint(
        ["company_id", "document_id", "project_id"],
        ["documents.company_id", "documents.id", "documents.project_id"],
    ),
    CheckConstraint(
        "jsonb_typeof(actions) = 'array' AND actions <@ '[\"read\", \"write\"]'::jsonb",
        name="supported_actions",
    ),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("event_id", String(36), primary_key=True),
    Column("company_id", String(128), nullable=False),
    Column("project_id", String(128), nullable=False),
    Column("employee_id", String(128), nullable=False),
    Column("agent_id", String(128), nullable=False),
    Column("request_id", String(128), nullable=False),
    Column("action", String(5), nullable=False),
    Column("document_id", String(128), nullable=False),
    Column("version_id", String(128), nullable=False),
    Column("allowed", Boolean, nullable=False),
    Column("reason", String(64), nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("result", String(32), nullable=False),
    Column("result_version_id", String(128), nullable=True),
    Column("result_version_number", Integer, nullable=True),
    Column("replayed", Boolean, nullable=False, server_default=text("false")),
    CheckConstraint("action IN ('read', 'write')", name="supported_action"),
    CheckConstraint("allowed = (reason = 'allowed')", name="consistent_authorization"),
    CheckConstraint(
        "result IN ('read', 'written', 'denied', 'version_conflict', "
        "'idempotency_conflict')",
        name="supported_result",
    ),
)

write_requests = Table(
    "write_requests",
    metadata,
    Column("company_id", String(128), ForeignKey("companies.id"), primary_key=True),
    Column("employee_id", String(128), primary_key=True),
    Column("agent_id", String(128), primary_key=True),
    Column("request_id", String(128), primary_key=True),
    Column("fingerprint", String(64), nullable=False),
    Column("document_id", String(128), nullable=False),
    Column("result_version_id", String(128), nullable=False),
    Column("result_version_number", Integer, nullable=False),
    ForeignKeyConstraint(
        ["company_id", "document_id", "result_version_id"],
        ["versions.company_id", "versions.document_id", "versions.id"],
    ),
    CheckConstraint("result_version_number BETWEEN 1 AND 1000000", name="number_range"),
    CheckConstraint("fingerprint ~ '^[a-f0-9]{64}$'", name="sha256_fingerprint"),
)
