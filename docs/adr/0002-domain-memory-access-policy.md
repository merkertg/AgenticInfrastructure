# ADR 0002: Pure Python access policy and audit-attempt metadata

- Status: accepted
- Date: 2026-10-24

## Context

Shared corporate memory needs explicit rules for company and project boundaries,
employee document rights and the authority delegated to an agent. A health
endpoint cannot exercise these rules. Introducing storage or a model provider
first would make it harder to separate policy defects from infrastructure.

Stage 02 needs a reproducible offline scenario. It must not present caller-supplied
employee identifiers as authenticated identity or an in-memory event as a durable
audit trail.

## Decision

Use frozen, slotted standard-library dataclasses for companies, projects,
employees, employee-bound agents, delegations, documents, versions, grants,
requests and decisions. Validate identifiers, metadata, status flags and bounded
document text when constructing objects. Hide document content from the version's
automatic representation; this is a convenience, not access control or redaction.

Represent actions and decision reasons with `StrEnum`. Grant and delegation
actions are `frozenset[Action]`; `read` and `write` do not imply one another.
Empty action sets authorize nothing. A document grant applies to one employee,
company, project and document. Delegation applies to one registered agent and
employee in one company/project.

`evaluate_access()` is a pure function over one explicit snapshot: a request and
the supplied company, project, employee, agent, delegation, document, version and
grant tuple. It checks scope and active object relationships before requiring
both employee permission and delegated action. Missing or mismatched prerequisites
produce a deterministic denial with the first applicable reason. Malformed input
types and ambiguous duplicate grant identifiers are programming/input errors,
not successful decisions. The function returns request metadata and outcome; it
does not return content or execute a read/write.

`make_audit_event()` builds a frozen authorization-attempt event from a decision
and an explicitly supplied timezone-aware timestamp, normalized to UTC. It has no
system-clock or storage access and includes no document text. The event describes
the policy attempt; it does not claim that an operation or response delivery
completed.

Use fictional fixtures and a deterministic command-line demo. Keep domain code
independent of FastAPI, Pydantic, configuration, persistence and model SDKs.
Leave the HTTP API unchanged until durable audit and verified identity exist.

## Consequences

Policy behavior can be tested without a server, database, credentials or external
service. Immutable objects make changes explicit: revoked access is represented
by a new snapshot with an inactive grant or delegation, then reevaluated.
Decisions are snapshots and must not be reused as permanent authorization.

The caller remains responsible for loading complete, current and trusted objects.
Stage 02 neither establishes actor identity nor verifies object provenance.
Later application and storage layers must preserve these boundaries, coordinate
concurrent changes and persist required events before business success.

There is no durable memory, audit append operation, transaction, historical
version store, authenticated API or retrieval mechanism yet. Creating a
`DocumentVersion` object is not uploading a document into a corporate service.
