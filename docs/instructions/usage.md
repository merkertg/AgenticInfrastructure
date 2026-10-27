# Current API and memory demonstrations

[← README](../../README.md) · [All documentation](../../README.md#documentation) · [Quickstart](quickstart.md)

The HTTP API exposes liveness and three authenticated memory operations:

| Method | Path | Result |
| --- | --- | --- |
| GET | `/health` | `200`, `{"status":"ok","service":"agentic-infrastructure"}` with default settings |
| GET | `/memory/projects/{project_id}/documents/{document_id}/versions/{version_id}` | Authorized version, bearer credential required |
| POST | `/memory/projects/{project_id}/documents/{document_id}/versions` | Append the next version, bearer credential and `Idempotency-Key` required |
| POST | `/memory/projects/{project_id}/context` | Ranked passages from currently readable documents, bearer credential required |

`/health` is liveness only: the current HTTP application can answer a request.
It performs no database, identity, connector, memory, authorization or LLM probe
and records no audit event. It works without `APP_DATABASE_URL`.

Memory routes derive the actor from the server-stored credential, never from
request fields or headers. See [memory API usage](usage.md#authenticated-memory-api) for lab
credentials, status codes and limits, and [context search](usage.md#document-intake-and-context-search)
for document intake and search.

`POST /health` returns `405`. `/`, `/memory` and `/agents` return `404`.
`/docs`, `/redoc` and `/openapi.json` provide generated API documentation.

| Setting | Default | Meaning |
| --- | --- | --- |
| `APP_NAME` | `agentic-infrastructure` | Service title and health response name; whitespace is trimmed and empty names are rejected |
| `APP_ENV` | `development` | Validated label: `development`, `test` or `production` |
| `APP_DEBUG` | `false` | Explicit local traceback/debug option |
| `APP_DATABASE_URL` | unset | PostgreSQL URL for memory routes; secret in settings output; memory routes return `503` when unset |

Configuration precedence: explicit `Settings(...)` values, process environment,
local `.env`, defaults. Settings are cached by `get_settings()` for the process.
Restart the server after changes. `create_app(settings)` allows tests to inject a
separate configuration. `APP_ENV` does not change permissions or install an
identity provider; leave debug disabled when handling any sensitive information.

The [roadmap](../ROADMAP.md) describes future shared-memory behavior. HTTP
memory access currently covers stored versions and context search over
fictional lab documents; no model, agent tool or corporate source exists.

For the Stage 02 access-policy demonstration, run from the repository root:

```bash
uv run --locked python -m app.demo
```

The demo evaluates fictional authorization attempts and constructs audit-event
metadata in memory. It needs no server, environment settings, corporate data,
database or model. It performs no content read/write or durable audit recording.
See [domain usage](usage.md#offline-domain-and-access-policy) and the [Russian guide](../STAGE_02.md).

For the Stage 03 PostgreSQL laboratory, first create a separate local lab database
and set its connection explicitly:

```bash
export DATABASE_URL='postgresql+psycopg://agentic@127.0.0.1:55435/agentic_lab'
uv run --locked alembic upgrade head
uv run --locked python -m app.persistence_demo --setup
uv run --locked python -m app.persistence_demo
```

Use `--setup` once in a new database ending in `_lab`; rerun the demo without it
to use preserved data. The trusted Python path can read a permitted stored
version and append another
version with audit and write-replay records in one transaction. That trusted
path does not authenticate supplied employee IDs; the Stage 04 HTTP routes use
the same store with a verified credential. See
[persistence usage](usage.md#postgresql-persistence) and the [Stage 03 guide](../STAGE_03.md).


## Offline domain and access policy

Run the fictional demonstration from the repository root:

```bash
uv sync --locked
uv run --locked python -m app.demo
uv run --locked pytest tests/domain
```

The demo imports the domain, not the web application or settings. No server,
`.env`, network, database or credentials are required. All cases use fixed
fictional objects and a supplied timestamp.

### Contract

`app/domain/models.py` defines immutable dataclasses and explicit action/reason
enums. `app/domain/policy.py` evaluates the supplied snapshot:

```python
decision = evaluate_access(
    request,
    company=company,
    project=project,
    employee=employee,
    agent=agent,
    delegation=delegation,
    document=document,
    version=version,
    grants=(grant,),
)
```

An allowed decision requires an active, matching company/project, employee,
employee-bound agent, delegation, document and version. The requested action must
be in both the delegation and a current matching employee document grant.
`Action.READ` and `Action.WRITE` are independent. A missing or revoked grant
does not inherit access from another employee, document or project.

The function returns an `AccessDecision` containing the exact request, a boolean
outcome and a `ReasonCode`. It returns no document content and performs no
mutation. Missing or inactive prerequisites deny access. Malformed snapshot types
or duplicate grant identifiers raise an input error; they do not authorize a
request. The function performs no authentication or lookup of current rights.

Use `dataclasses.replace()` to create a changed immutable snapshot, then call the
policy again. An old allowed decision is not a reusable permission token.

### Audit-attempt model

```python
event = make_audit_event(decision, occurred_at=aware_timestamp)
```

`app/domain/audit.py` copies request/outcome metadata and normalizes an explicit
timezone-aware `datetime` to UTC. A naive datetime is rejected. No clock is read
and no event is written to disk or a database. The event describes an
authorization attempt, not a completed read, write or delivered response.

Identifiers in these offline models are input metadata. They do not prove the
employee's identity, the authenticity of the agent registration or the freshness
of grants. Trusted loading, durable audit and authenticated HTTP operations
are implemented in the storage adapter and memory HTTP routes described below.

`DocumentVersion.content` is bounded and excluded from `repr()`, but the field
is still accessible to ordinary Python code. Never treat this convenience as a
redaction or access-control boundary. Use only fictional data in this stage.


## PostgreSQL persistence

Stage 03 provides trusted Python operations to read an existing version or append
a new version to an existing document. It loads current policy objects from
PostgreSQL and persists audit metadata. Caller-supplied actor IDs are not
authenticated identities on this path. Stage 04 adds hashed agent credentials
(migration `0002_agent_credentials`) and HTTP routes that pass a verified
credential to the same store; see [memory API usage](usage.md#authenticated-memory-api).

### Connect and migrate

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

### Operation boundary

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
- An optional `credential_id` (passed by the HTTP routes) is rechecked after the
  company lock: an inactive, expired or mismatched credential denies with
  `credential_unavailable`, and the audit row records the credential ID.

The current adapter locks the company row during operations. It also reports
stale expected revisions and version-identity collisions as conflicts. This
coarse locking scheme describes the current implementation, not a scalability
claim. It explicitly uses `READ COMMITTED` so statements after a lock wait see
the earlier transaction's committed state. Direct database administration can
bypass application behavior and is
outside the agent contract.

### Integration checks

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


## Authenticated memory API

Stage 04 exposes two memory operations to agents holding a server-issued bearer
credential. The server derives company, employee and agent from the stored
credential and agent registration. Request paths, query parameters, bodies and
other headers never select the actor. This is a learning credential scheme, not
corporate single sign-on.

### Prepare a lab

Use a migrated local lab database ending in `_lab` (see
[persistence usage](usage.md#postgresql-persistence)), then issue fictional credentials:

```bash
export DATABASE_URL='postgresql+psycopg://agentic@127.0.0.1:55435/agentic_stage04_lab'
uv run --locked python -m app.persistence_demo --setup
uv run --locked python -m app.credential_lab fin-agent
uv run --locked python -m app.credential_lab eng-agent --hours 2
```

Each command prints a token once. PostgreSQL stores only its SHA-256 digest,
the agent/employee binding, the issue and expiry times and an `active` flag.
Lifetimes are 1–168 hours (default 8). Keep tokens out of Git, logs and reports.

Start the API with an explicit web database URL:

```bash
export APP_DATABASE_URL="$DATABASE_URL"
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8005
```

Without `APP_DATABASE_URL`, memory routes return `503`. `/health` never probes
the database. Bearer tokens require TLS outside loopback; this stage adds none.

### Operations

| Method | Path | Success |
| --- | --- | --- |
| GET | `/memory/projects/{project_id}/documents/{document_id}/versions/{version_id}` | `200` version with content and `Cache-Control: no-store` |
| POST | `/memory/projects/{project_id}/documents/{document_id}/versions` | `201` new version metadata; `200` with `replayed: true` for an identical retry |

Both require `Authorization: Bearer <token>`. `POST` also requires
`Idempotency-Key` and a body with exactly `base_version_id`, `expected_number`
and `content` (1–20,000 characters, no NUL). The server derives the new version
ID (UUID 5 of company, document and number) and sets `source` to
`agent:<agent_id>`.

```bash
curl -H "Authorization: Bearer $FIN_TOKEN" \
  http://127.0.0.1:8005/memory/projects/finance/documents/policy/versions/policy-v1
```

### Refusals

| Status | Meaning |
| --- | --- |
| `401` | Missing, malformed, unknown, revoked or expired credential; same body for all |
| `404` | Any authorization denial or missing target; same body for all |
| `409` | `version_conflict` or `idempotency_conflict` |
| `422` | Invalid path, body or `Idempotency-Key`; checked after authentication |
| `503` | Storage not configured, unavailable, or mandatory audit failed |

Every request that reaches the policy check is audited with the credential ID,
including denials and conflicts. `401` and `422` requests are not business
audit rows. After locking the company, the store rechecks the credential, so a
revocation committed after authentication denies the operation with
`credential_unavailable`.

### Limits

No rate limiting, lockout, failed-login history, credential management API,
document listing or latest-version lookup exists yet. Credential issuance is
privileged lab setup. A stolen token acts as its agent until expiry or
revocation. Use [context search](usage.md#document-intake-and-context-search) to find passages and their
version IDs.


## Document intake and context search

Stage 05 imports fictional UTF-8 text documents through trusted lab setup and
lets authenticated agents search the documents they may currently read.

### Import documents

```bash
export DATABASE_URL='postgresql+psycopg://agentic@127.0.0.1:55435/agentic_stage05_lab'
uv run --locked python -m app.persistence_demo --setup
uv run --locked python -m app.intake_lab --project finance --grant fin-user:read fixtures/lab/finance/*.txt
```

Each file becomes a document (ID and title from the file name) with version 1
and source `file:<name>`. `--grant EMPLOYEE:read[,write]` grants every document
of the batch; repeat it for more employees. The format is strict UTF-8 plain
text up to 64 KiB and 20,000 characters; a BOM is removed and line endings
become `\n`. Rejection codes: `too_large`, `not_utf8`, `control_characters`,
`empty`, `invalid_metadata`, `duplicate_document`, `document_exists`,
`project_unavailable`, `employee_unavailable`, `project_document_limit`,
`batch_size`. Any problem rejects the whole batch (at most 50 files); the
command prints all problems and exits with `1`. A project holds at most 500
documents. The command accepts only a local database ending in `_lab`.

### Search

```bash
curl -s -X POST -H "Authorization: Bearer $FIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"travel limit receipt","max_items":3}' \
  http://127.0.0.1:8005/memory/projects/finance/context
```

The body has only `query` (one line, 1–256 characters, 1–16 distinct words)
and `max_items` (1–10, default 5). The response has `status` (`found` or
`no_authorized_evidence`), `truncated` and `items`. Each item has
`document_id`, `version_id`, `version_number`, `source`, `fragment`, `start`,
`end`, `score` and `text`; `content[start:end] == text` for that version.
Returned text totals at most 4,000 characters. Responses use
`Cache-Control: no-store`.

| Status | Meaning |
| --- | --- |
| `200` | Search ran; `no_authorized_evidence` means no readable document matched |
| `401` | Invalid or missing credential |
| `404` | No read delegation for the project, or an unknown or foreign project |
| `422` | Extra fields or invalid query or `max_items` |
| `503` | Storage unavailable or context history could not be stored |

### Guarantees and limits

The project scope and each document are authorized before ranking; text of
unreadable documents is not loaded and does not influence BM25 statistics.
Selected documents are authorized again before the response. Fragments are
computed per request, and no index or result cache is stored, so revocation,
deactivation and new versions apply immediately. Search uses the latest active
version of each document and at most 200 readable documents ordered by ID.

`context_requests` stores scope, decision, result, counts and the query's
SHA-256, not the query text; `context_items` stores returned version and
fragment references, not text. Lexical ranking has no synonyms or semantics.
