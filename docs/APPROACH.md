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

## Implemented foundation and offline domain

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

`GET /health` is liveness only. No business endpoint is exposed. Tests use
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

## Planned architecture

Grow a modular monolith as working behavior appears:

| Layer | Planned responsibility | First stage |
| --- | --- | --- |
| Domain | Implemented offline objects, deterministic policy and audit-event model | 02 |
| Application | Persistent memory operations and audit requirements | 03 |
| Persistence | Transactional document versions, grants, idempotency and audit | 03 |
| Identity and API | Verified actor identity, agent delegation and scoped memory operations | 04 |
| Retrieval | Bounded ingestion, permission-filtered search and context provenance | 05 |
| Agent tool | Bounded context reads through existing application operations | 06 |
| Corporate adapter | One read-only source, permission synchronization and revocation | 07 |
| Delivery | Containers, integration evaluations and operating instructions | 08 |

Domain code uses ordinary Python types without FastAPI, database or model SDK
imports. Storage adapters will implement explicit application contracts.
PostgreSQL is planned for durable transactions. Search technology and a model
provider will be selected when their stages have concrete requirements. Empty
future layers and unused dependencies are not added in advance.

## Identity and access boundaries

The policy model is exercised offline with fictional actors and documents.
Its actor identifiers are input metadata, not authentication. Callers must supply
the relevant current objects; the domain does not establish their trusted origin.
Before
memory operations are exposed over HTTP, the server must verify credentials and
bind the resulting employee identity to a registered active agent in the same
company and its valid delegation. An arbitrary request field or header cannot
select a trusted employee identity.

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

## Audit model and planned durable history

`make_audit_event()` creates an immutable description of an authorization attempt
from its decision and a supplied timezone-aware timestamp normalized to UTC.
It copies request identifiers, action, outcome and reason without document text.
It reads no clock and performs no I/O. The event is not persisted and does not
prove that a document was read, changed or delivered. In-memory `active` flags
and new snapshots represent revocation in Stage 02; there is no live grant store.

Planned business operations will carry a server-established actor and agent,
company/project scope, operation, target/version references, outcome, reason code,
timestamp and request identifier. Audit records should contain the metadata needed
to explain an action without copying credentials or full sensitive documents.

Successful mutations and their audit record must commit in one transaction.
Denied attempts and authorized context-read attempts also need durable history.
A read record does not prove that the client received the response. If required
audit recording is unavailable, business data must not be returned and mutations
must fail closed; that outage may also prevent a durable denial record. The
liveness endpoint still describes whether the HTTP service can respond. The
exact failure and recovery contracts will be defined and tested in the
persistence stage.
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

Later stages must demonstrate allowed finance and engineering reads, denied
cross-project and cross-company requests, forged actor fields, expired or revoked
delegation, revoked document access, stale cache/index entries, unavailable audit
storage, malformed input and instruction-like document text. Expected results
must be independent of the implementation and run on fictional fixtures.

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
