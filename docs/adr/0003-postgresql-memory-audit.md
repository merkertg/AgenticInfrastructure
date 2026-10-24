# ADR 0003: PostgreSQL versions, transactional audit and request replay

- Status: accepted
- Date: 2026-10-24

## Context

Stage 02 evaluates explicit in-memory objects. It cannot preserve a document
revision between processes, establish the current stored grant, or guarantee that
a successful mutation has a durable history entry. Persistence must preserve the
same company, project, employee and delegated-agent boundaries before an HTTP
memory API is introduced.

The next small result is reading an existing version and appending a new version
to an existing document through a trusted Python caller. Document intake,
authenticated HTTP requests and administration of employee rights remain later
work. Fictional bootstrap records are privileged laboratory setup.

## Decision

Use PostgreSQL, SQLAlchemy Core with psycopg, and Alembic migrations. Keep the
pure Python domain independent of these libraries. Store companies, projects,
employees, agents, delegations, documents, versions and grants. Composite keys
and foreign keys retain the company boundary; additional references bind a
document grant to its project and an agent delegation to its employee.

Load the policy input from the database for each memory operation and reuse
`evaluate_access()`. A requested employee or agent ID still does not authenticate
the caller. Keep the HTTP application limited to liveness until Stage 04.

Serialize the current adapter's operations for a company with a row lock on its
company record. This is a deliberately coarse local design, not a claim about
high throughput. Use PostgreSQL `READ COMMITTED` explicitly, including when the
store receives an externally created engine. After waiting for a company lock,
later statements must see the preceding transaction's committed versions and
write-replay record instead of retaining an older transaction snapshot.
Version appends also require the expected current revision
number. The adapter inserts a new version; it provides no version-overwrite API.

An allowed read must persist its audit record before returning content. A version
append, its audit record and its idempotency record commit in one transaction.
Denied attempts and version/idempotency conflicts also receive audit metadata.
If mandatory audit insertion or commit fails, no successful result is returned;
an append and its idempotency record roll back. The same outage may prevent a
durable denial record.

Scope write replay by company, employee, agent and request ID. Fingerprint the
request and write payload, excluding the attempt timestamp. An identical retry
returns the original version metadata without creating another version. A changed
payload under the same key is an idempotency conflict. Reevaluate current access
before using an earlier result, and audit each valid attempt reaching the policy
check. Malformed operation arguments fail before the transaction and are not
recorded as business operations. A cached result is not a
permission token. Read and write remain separate actions; a write result contains
version metadata rather than granting a content read.

Audit rows contain actor/target identifiers, action, authorization decision,
operation result, time and resulting version metadata. They contain no document
body, source text or credentials. Missing-object denials must be recordable, so
audit actor/target fields are not foreign keys to records that may not exist.
Audit storage is application history; it is not tamper-proof against a privileged
database owner.

Use an isolated disposable PostgreSQL database for integration tests. Run unit
and PostgreSQL checks separately in CI. Integration test setup rejects unsafe
URLs before destructive reset and never falls back to a developer's application
connection. Report actual local database and remote CI evidence separately.

## Consequences

The project can now preserve versions and operation history independently of the
web process and verify rollback, request replay and version conflicts with a real
database. Database constraints supplement application checks; neither replaces
authentication or complete deployment security.

The current lock serializes a company's memory operations. Administrative or
external direct SQL access is privileged and outside the agent-facing contract;
future administration paths must coordinate policy changes with the same
transaction boundary. The database owner can still alter data directly.

The initial adapter appends to an existing document seeded through trusted lab
setup. It does not expose general document intake, deletion, grant management,
search or a corporate connector. Stronger operating roles, recovery, source sync
and production deployment require their own stages and evidence.
