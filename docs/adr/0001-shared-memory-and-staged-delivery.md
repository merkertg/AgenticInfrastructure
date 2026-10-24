# ADR 0001: Shared corporate memory and staged delivery

- Status: accepted
- Date: 2026-10-24

## Context

The `#AgenticInfrastructure` assignment offers shared corporate memory or
inter-agent discovery and task exchange. A focused learning build needs one
observable scenario and explicit access boundaries. Shared memory lets agents of
different employees reuse authorized company knowledge while retaining source
provenance and a history of actions.

The neighboring AI Forge projects use small verified stages, explanatory guides
and a clear distinction between implementation, local results and remote
evidence. The repository initially contains only the assignment description.

## Decision

Choose shared corporate memory. Start with fictional finance and engineering
projects in company Alpha and a separate company for isolation checks. Agent
discovery, task exchange and general corporate write automation are outside this
roadmap.

Stage 01 provides Python 3.12, uv, FastAPI, validated settings, an application
factory and a strict liveness response. Keep the code small and add layers only
with their first working behavior. Use locked dependencies, isolated tests,
Ruff, strict mypy and documentation checks.

Build deterministic domain policies offline before adding PostgreSQL persistence
and transactional audit. Actor IDs in this offline model are metadata. Expose
memory operations over HTTP only after server-verified identity, active agent
delegation, current permission checks and durable audit exist.

Use deny-by-default access with company, project and action scope. An agent's
effective rights cannot exceed the employee's current rights or its delegation.
Filter retrieval before ranking and context construction, and recheck permission
before returning the assembled context. Propagate revocation and deletion to
derived data and caches. Returned context must cite actual source versions.
Required audit failure prevents business operations from succeeding.

Introduce bounded agent context reads only through the existing application
operations. Document text remains untrusted data. Add one read-only corporate
source after local contracts and revocation behavior are tested. Select retrieval
technology and a model provider when their implementation stages require them.

Use English for code, README, operational instructions and ADRs. Use accessible
Russian for stage guides, the roadmap, development log and verification. Record
actual checks, publication state and observed CI results separately.

## Consequences

Stage 01 demonstrates a runnable API foundation. It does not demonstrate shared
memory, authenticated employees, authorization or an audit trail. The service's
`/health` response and `production` label do not establish production readiness.

Later stages must test access denial and revocation as well as permitted reads.
Database isolation, retrieval indexes, caches, agent calls and corporate sync all
need the same policy boundary. The design is more work than a search endpoint
with a caller-supplied employee ID, but it gives concrete contracts to verify.

The initial implementation remains a learning service. Production deployment,
corporate identity integration, operational recovery and protection of audit
history against privileged administrators require their own evidence and review.
