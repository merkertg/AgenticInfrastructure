# PostgreSQL persistence

[README](../../README.md) · [Russian Stage 03 guide](../STAGE_03.md) · [ADR](../adr/0003-postgresql-memory-audit.md)

Stage 03 provides trusted Python operations to read an existing version or append
a new version to an existing document. It loads current policy objects from
PostgreSQL and persists audit metadata. The HTTP API still exposes only health;
caller-supplied actor IDs are not authenticated identities.

## Connect and migrate

Use an isolated local PostgreSQL laboratory. Set the connection explicitly;
`DATABASE_URL` is separate from the web application's `APP_*` settings:

```bash
uv sync --locked
export DATABASE_URL='postgresql+psycopg://agentic@127.0.0.1:55435/agentic_lab'
uv run --locked alembic upgrade head
uv run --locked python -m app.persistence_demo --setup
uv run --locked python -m app.persistence_demo
```

The lab CLI accepts only loopback PostgreSQL URLs selecting a database ending in
`_lab`, without URL query parameters. Run `--setup` once: it applies migrations
and inserts fictional records when no company has been populated. Subsequent
runs omit that flag and use saved data; they do not clear the database.

The migration creates the current schema and records its Alembic revision. It
does not open a memory endpoint or start authentication. Connection strings and
local data are not committed. The detailed guide includes native PostgreSQL lab
setup and explains the demo's fictional bootstrap.

## Operation boundary

- Reads return a version only after authorization and mandatory audit commit.
- Writes append a version with an expected current revision number. They return
  version metadata and preserve the old revision.
- A write, its audit row and its idempotency row commit together. Audit failure
  prevents a successful read result and rolls back a write.
- Reusing the same company/employee/agent/request key and payload replays the
  original write result. A different payload produces an idempotency conflict.
- Access is checked again before replay. Revoked rights do not inherit an old
  success. Every valid attempt reaching the policy check gets a new audit row
  when audit storage is available. Malformed operation arguments are rejected
  before a transaction and are not business-audit rows.

The current adapter locks the company row during operations. It also reports
stale expected revisions and version-identity collisions as conflicts. This
coarse locking scheme describes the current implementation, not a scalability
claim. It explicitly uses `READ COMMITTED` so statements after a lock wait see
the earlier transaction's committed state. Direct database administration can
bypass application behavior and is
outside the agent contract.

## Integration checks

Use a separate disposable database whose name ends in `_test`:

```bash
export TEST_DATABASE_URL='postgresql+psycopg://agentic@127.0.0.1:55435/agentic_test'
uv run --locked pytest -m postgres
```

The test fixture validates a local PostgreSQL URL before resetting its test
schema. A configured but unavailable database fails the tests. An absent test URL
skips PostgreSQL tests in ordinary local runs; that is not evidence that database
behavior passed. Do not substitute an application or corporate database.

Run checks without a database with:

```bash
uv run --locked pytest -m 'not postgres'
```

See the [verification report](../VERIFICATION.md) for the database, migration,
rollback, concurrency and restart checks actually performed.
