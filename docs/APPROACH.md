# Approach

[README](../README.md) · [Roadmap](ROADMAP.md) · [Architecture decision](adr/0001-shared-memory-and-staged-delivery.md)

## Product scenario

Build a shared corporate memory service for agents acting on behalf of employees.
The initial fictional company, Alpha, has finance and engineering projects. A
finance employee's assistant can retrieve authorized finance context; an
engineering employee's assistant can retrieve authorized engineering context.
Neither gains access merely by knowing a document, employee or project ID.

The eventual context response will contain bounded excerpts and source references
to specific document versions. It will state when no authorized evidence exists.
Agents share the service while retaining the access boundaries of the employees
who delegated to them. Agent discovery and task exchange are outside this build.

## Implemented foundation, domain, persistence and memory API

Stage 01 contains a small FastAPI application:

```text
client → Uvicorn → FastAPI → health handler → HealthResponse → JSON
                         ↑
                  create_app(Settings)
```

`app/config.py` validates settings and caches the normal process-level load.
`create_app()` accepts explicit settings so tests can build independent
applications. `app/main.py` owns the factory, response contract and health handler.
The default response contains `status: "ok"` and
`service: "agentic-infrastructure"`.

`GET /health` is liveness only and never probes the database. Tests use
in-process clients and isolated settings. The `production` environment label does
not add production deployment behavior.

Stage 02 adds a pure Python domain independent of the web application:

```text
fictional immutable objects + AccessRequest
                    ↓
             evaluate_access()
                    ↓
        AccessDecision + supplied time
                    ↓
             make_audit_event()
```

The models cover companies, projects, employees, registered employee-bound
agents, delegations, documents, versions and employee document grants. Models
validate bounded values and use frozen dataclasses; permitted actions are
immutable sets of explicit `read` and `write` enum values.

The policy checks supplied scope and object relationships, then intersects the
employee's document permission with the agent's project/action delegation.
Absent, inactive or mismatched prerequisites deny access with an explicit reason.
It does not fetch data, authenticate an employee, execute a read/write or save an
audit record. The separate demo runs without web settings or external services.
See [ADR 0002](adr/0002-domain-memory-access-policy.md).

Stage 03 adds PostgreSQL persistence through a separate trusted Python caller:

```text
AccessRequest + optional new revision
                  ↓
      transaction + company row lock
                  ↓
 stored policy objects → evaluate_access()
                  ↓
 version read/append + audit + write replay record
                  ↓
         commit → MemoryResult
```

SQLAlchemy Core and psycopg implement the adapter; Alembic defines schema
history. Reads return an authorized stored version after audit commit. Writes
append to an existing document and return version metadata. A write's version,
audit row and idempotency row commit together. Current access is checked before
an identical write retry may reuse an earlier result. Expected revision numbers
detect stale changes. See [ADR 0003](adr/0003-postgresql-memory-audit.md).

Stage 04 connects these operations to HTTP for authenticated agents:

```text
Authorization: Bearer token
        ↓
SHA-256 lookup in agent_credentials → Principal
        ↓
AccessRequest from Principal + path (no actor fields accepted)
        ↓
MemoryStore: company lock → credential recheck → policy → audit → commit
        ↓
200/201 with data, uniform 404 denial, 409 conflict or 503 failure
```

A credential is an opaque random token bound to one agent registration and its
employee, with mandatory expiry and revocation. Only its digest is stored. The
employee is derived from the server-side registration, so agents cannot select
another employee or exceed that employee's grants intersected with their
delegation. Unauthenticated requests receive one uniform `401` and are not
business-audited. The server derives new version IDs and `agent:<id>`
provenance. See [ADR 0004](adr/0004-agent-credentials-memory-api.md).

Stage 05 adds trusted plain-text intake and context search:

```text
POST /memory/projects/{project}/context + bearer token
        ↓
company lock → credential recheck → evaluate_scope()
        ↓
readable documents → evaluate_access() → load authorized text only
        ↓
fragments → BM25 over authorized fragments → bounded selection
        ↓
evaluate_access() again → context history → commit → items or explicit empty
```

Fragments are computed per request from the latest active version, so no
derived index or cache can outlive a revocation. History stores references and
a query digest, not text. See [ADR 0005](adr/0005-permission-filtered-context.md).

Stage 06 gives a model one tool over that search:

```text
POST /memory/projects/{project}/answers + bearer token
        ↓
scope check (model not started when denied)
        ↓
model step → search_memory(query, max_items) → strict argument check
        ↓ (scope from path and token)
Stage 05 search → result to the model → … at most 4 steps, 3 calls, 15 s
        ↓
answer: text + citations ⊆ references returned in this run
        ↓
company lock → scope and cited versions rechecked → run history → commit
```

The model never sees or chooses the project, employee or agent. Uncited text is
not returned. Losing access to a cited version withholds the whole answer. The
first provider is deterministic and offline. See
[ADR 0006](adr/0006-bounded-agent-context-tool.md).

## Architecture and remaining stages

Grow a modular monolith as working behavior appears:

| Layer | Responsibility | First stage |
| --- | --- | --- |
| Domain | Implemented offline objects, deterministic policy and audit-event model | 02 |
| Application | Implemented result contracts, authenticated principal and bounded agent loop | 03–06 |
| Persistence | Implemented PostgreSQL versions, grants, credentials, write replay and audit | 03–04 |
| Identity and API | Implemented agent bearer credentials and scoped memory version routes | 04 |
| Retrieval | Implemented bounded intake, permission-filtered search and context provenance | 05 |
| Agent tool | Implemented one scoped search tool, citation checks and an offline provider | 06 |
| Corporate adapter | One read-only source, permission synchronization and revocation | 07 |
| Delivery | Containers, integration evaluations and operating instructions | 08 |

Domain code uses ordinary Python types without FastAPI, database or model SDK
imports. The PostgreSQL adapter implements explicit memory-operation results.
Lexical search and an offline
provider are implemented; a live model provider will be selected separately with
its own evaluations. Empty
future layers and unused dependencies are not added in advance.

## Identity and access boundaries

The policy model remains independently testable on fictional objects. Stage 03
loads current policy records from PostgreSQL; its trusted Python callers supply
actor IDs. Stage 04 HTTP routes verify a stored agent credential, derive the
employee from that agent's registration and require the registration and its
delegation to be active in the same company. An arbitrary request field or
header cannot select a trusted employee identity. The employee is not yet
authenticated interactively; corporate identity integration is future work.

The offline effective permission is the intersection of the supplied employee
rights, the agent's delegation scope and the requested company/project/action.
Unspecified access is denied. Read and write grants are separate. Cross-company
and cross-project identifiers must be checked by the server on every operation.
Administration and corporate source credentials are not given to model tools.

Retrieval must filter authorized documents before ranking and constructing
context. Denied content must not appear in snippets, counts, citations, errors or
model input. Derived chunks, search indexes and caches need the same boundaries
as their source documents. Recheck authorization before returning assembled
context so a revocation during retrieval cannot expose an earlier candidate.
Revocation and document deletion must invalidate or exclude stale derived context;
a cached success cannot authorize a later request. Cache keys and validation must
isolate company, actor, agent and policy revision, rather than only query text.

## Audit model and durable operation history

`make_audit_event()` creates an immutable description of an authorization attempt
from its decision and a supplied timezone-aware timestamp normalized to UTC.
It copies request identifiers, action, outcome and reason without document text.
It reads no clock and performs no I/O. The event is not persisted and does not
prove that a document was read, changed or delivered. In-memory `active` flags
and new snapshots represent revocation in the offline Stage 02 example.

The Stage 03 adapter loads stored grants on each operation. Its audit table also
records the memory-operation result, resulting version metadata and whether a
write was replayed. An authorized request can still produce a version or
idempotency conflict. Audit rows have no actor/target foreign keys because
denials for missing objects must remain recordable. No document body or source
text is copied into audit or idempotency payloads.

The adapter appends a new version, audit and write-replay record in one
transaction. It commits mandatory audit before returning read content. Audit
or commit failure prevents a successful result; write changes roll back. It
serializes current operations by locking the company row and also checks the
expected version number. The adapter explicitly uses `READ COMMITTED`, including
for externally supplied engines, so statements after a lock wait see the prior
operation's committed version and replay record. Direct privileged SQL
administration can bypass these application operations and is outside the agent
contract. Future rights-management
paths must coordinate changes with the transaction boundary.

HTTP operations carry a server-established actor, agent and credential ID,
company/project scope, operation, target/version references, outcome, reason code,
server timestamp and request identifier. Audit rows contain the metadata needed
to explain an action without copying tokens, digests or document text.

These transaction guarantees currently apply to trusted version reads and
appends. Future context retrieval must preserve them. Denied attempts and
authorized context-read attempts also need durable history.
A read record does not prove that the client received the response. If required
audit recording is unavailable, business data must not be returned and mutations
must fail closed; that outage may also prevent a durable denial record. The
liveness endpoint still describes whether the HTTP service can respond. The
current failure behavior is exercised with PostgreSQL integration tests;
deployment recovery procedures belong to the delivery stage.
Application audit records alone do not prove tamper resistance against database
administrators; stronger retention and operational controls need separate work.

Each returned excerpt will retain source identity, document version and location.
No source will be invented when retrieval finds no authorized evidence. Changes
to source content or grants must not silently alter an old recorded reference.

## Agent and connector limits

The planned agent receives a context-reading tool with validated arguments,
server-enforced scope, timeouts and budgets for calls and output size. Document
text is untrusted data, including text that asks the model to change permissions,
reveal another project or call a corporate service. The model cannot authorize an
operation or bypass the application layer. Initial evaluations use an offline
deterministic provider; a live model result is reported separately.

The first corporate integration will read one allowlisted source with restricted
credentials. It must propagate source deletions and revoked rights before its
content is made available. Sync failures must remain visible and cannot silently
preserve access that the service can no longer establish. Arbitrary URLs,
unrestricted service calls and corporate writes are outside the initial connector.

## Acceptance targets and limits

Current domain and persistence checks use fictional fixtures for permission
boundaries, version conflicts, request replay and unavailable audit storage.
Stage 04 adds authenticated HTTP requests with invalid, revoked and spoofed
identity. Later stages must extend them to source sync, retrieval and stale
cache/index entries. Expected results must be independent of the
implementation. A database check is reported as verified only after an actual
PostgreSQL run, separately from the unit suite.

This is a learning build. Production readiness would require verified identity
integration, transport security, secret management, deployment hardening,
operational monitoring, recovery exercises and a review of the organization's
actual data policy. None is established by an offline access decision or by a
successful `/health`.

## Learning and delivery

Every stage has a small working result, meaningful checks, a Russian walkthrough,
updated instructions and factual verification. Code, README, operational
instructions and ADRs use English. Stage guides, the roadmap, development log and
verification use Russian.

Python 3.12, uv and `uv.lock` provide a reproducible foundation. Dependencies for
later stages are introduced alongside their implementation. Local results,
publication state and remote CI evidence are tracked separately for the relevant
revision in the [verification report](VERIFICATION.md).
