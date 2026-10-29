# ADR 0007: Read-only source snapshots and expiring access confirmation

- Status: accepted for Stage 07
- Date: 2026-10-29
- Scope: one fictional Handbook HTTP v1 contract and a local laboratory

## Context

Agents already read permission-filtered memory through authenticated version,
context and answer routes. A source adapter must preserve those boundaries when
source text and reader lists change. Connector payloads are untrusted; they
cannot name trusted actors, choose a host, write to corporate services or widen
agent delegation. Real corporate service behavior has not been verified.

## Decision

Trusted setup registers one fixed base URL, source space, company/project,
document prefix, confirmation window and explicit individual-user/employee
mapping. This setup is a guarded local lab CLI, not an agent tool. The connector
uses GET only, validates HTTPS certificates, permits plain HTTP only on loopback,
ignores proxy variables and never follows redirects or payload URLs. Source
credentials come from the process environment for each run and are not stored.
A real deployment must issue an account with read-only scope at the source;
GET-only client behavior does not establish server-side token privileges.

Collect a complete bounded listing and any new/changed revision texts outside a
database transaction. Strict JSON rejects duplicate keys, extra fields,
non-finite numbers, malformed UTF-8 and invalid identifiers. A missing final
page, duplicate document, cursor loop or limit violation fails the snapshot;
absence can indicate deletion only after a complete listing.

Pure Python rules plan creates, updates, unchanged entries, deletions and
withheld texts. The source revision is assumed immutable: unchanged revisions
are not refetched. Changed revisions with identical normalized text update
source state without creating a duplicate memory version. A malformed text
withholds that document and revokes its readers while the rest of the complete
snapshot can apply. Transport/listing failures apply nothing.

Apply the plan under the company row lock in one transaction with current source
settings, current account mapping, versions, document activity, read grants,
confirmation and the run record. Older competing snapshots are rejected as
`superseded`. Document-ID conflicts and the project document limit fail the
whole snapshot. The source owns grants for its mirrored documents: only mapped
individual users receive read grants, unresolved users/groups grant nothing and
other active grants are deactivated. Agent delegation and employee activity
remain separate required conditions. Memory appends to mirrored documents are
denied regardless of manual write grants.

A successful complete snapshot confirms access for `max_age_seconds` measured
from the run's start. A failed run leaves the earlier confirmation aging. Reads
require an active source and an unexpired confirmation. Search excludes expired
sources before text loading and ranking; final selected-document and answer
checks enforce the same gate. Old versions retain their original text and need
current access. There is no persisted index or result cache. Source revocation
is visible after the next successful sync, or at expiry if synchronization
fails; the window is an explicit bounded delay, not immediate source revocation.

Store source settings, trusted account mappings, mirrored state and run metadata
in four new tables. Run history contains only counts, timestamps, outcome and
failure code. Operator status returns confirmation times, document-state counts,
a bounded withheld-ID list and the latest run. No document body, source token,
model transcript or payload exception is logged in sync history. Database or
history failure prevents a successful application commit. Migration downgrade
must deactivate mirrored documents and their grants under company locks before
removing source metadata, so it cannot leave previously mirrored data readable
without its source gate. Historical version bodies are retained, not erased. Re-upgrade cannot reconstruct
source ownership and does not reactivate these documents; importing them again
requires trusted preparation.

## Limits and consequences

- Collection allows 20 pages, 100 documents per page, 200 documents per snapshot,
  256 KiB per listing page and 100 readers per document. Registration allows
  1,000 account mappings; the existing project limit is 500 documents.
- Text uses the existing 64 KiB/20,000-character plain UTF-8 contract.
- Requests receive at most 5 seconds across DNS, connect, headers and body;
  collection checks its 60-second deadline before/after requests and before
  returning a snapshot. A daemon worker bounds caller wait, shuts down timed-out
  sockets when possible and prevents a late connection from sending a canceled
  authenticated request. OS resolver calls may linger; four process-wide slots
  bound those workers. This is not operating-system resolver cancellation.
- Confirmation windows range from 60 to 86,400 seconds. Failed syncs may leave
  old rights usable until that deadline. Operators can disable a source sooner
  through trusted administration coordinated with the company lock.
- Listing consistency and immutable revision semantics are source-contract
  assumptions. No ETag or source-wide snapshot identifier is implemented.
- The first source is a fictional loopback server with fixture snapshots. Real
  identity/ACL mapping, credentials, network operations and corporate deployment
  require separate verification. No automatic scheduler, source administration
  HTTP API, source write operation, live model, cache or search index is added.

See the [Stage 07 guide](../STAGE_07.md), [usage](../instructions/usage.md#read-only-source-synchronization)
and [verification report](../VERIFICATION.md).
