# ADR 0006: Bounded agent context tool and offline model provider

- Status: accepted
- Date: 2026-10-29

## Context

Stage 05 lets an authenticated agent search permission-filtered passages. The
next step lets a model work on an employee's behalf with that memory. A model is
untrusted: it may follow instructions found in documents, invent references,
loop, fail or return oversized output. It must not choose the project, employee
or agent, reach the database, change rights or call arbitrary corporate
operations. Answers derived from passages are derived content and must not
survive a revocation that happens while the model works.

## Decision

Expose exactly one tool, `search_memory`, with arguments `query` (the Stage 05
query rules) and optional `max_items` (1–5, default 3). Arguments arrive as raw
JSON text of at most 1,024 characters and are parsed strictly: duplicate keys,
`NaN`, unknown keys (including `project_id` or `employee_id`), wrong types and
out-of-range values are rejected with a code (`unknown_tool`,
`arguments_too_large`, `invalid_json`, `invalid_arguments`). The code, never the
input, is returned to the model so it can correct itself. The server binds the
tool to the authenticated agent and the project from the path; every call is an
ordinary Stage 05 search with its own transaction, rechecks and history.

`POST /memory/projects/{project_id}/answers` accepts only a one-line question of
at most 500 characters. The route:

1. checks project scope with the current credential under the company lock and
   does not start the model when the scope is denied;
2. runs a provider-neutral loop (`run_agent`) with a monotonic deadline: at most
   4 model steps, 3 tool calls, 15 seconds and 24,000 transcript characters per
   run. A batch of calls that would exceed the remaining budget is refused
   whole; time spent in the provider and in tools both count;
3. accepts a final answer only if it is one of the two turn kinds, its text is
   1–2,000 characters without control characters other than tab and newline,
   and every citation (document, version, fragment) equals a reference returned
   by this run's searches, at most 10 and without duplicates;
4. returns no model text without citations (`unanswered`);
5. in one transaction under the company lock, rechecks the scope and every
   cited version with `evaluate_access()`, then records the run and commits.
   If a cited version lost access, the whole answer is withheld
   (`evidence_revoked`), because its text may rest on that passage.

A scope denial at any point ends the run as `denied` without passing anything to
the model. HTTP maps `denied` and `evidence_revoked` to the uniform `404` of the
memory API, `time_limit` to `504`, other failed outcomes (`provider_error`,
`invalid_turn`, `invalid_answer`, `invalid_citation`, `tool_call_limit`,
`step_limit`, `transcript_limit`) to `502` with the outcome code only. Provider
exceptions are logged by type only.

Run history stores scope, credential ID, provider name, outcome, reason,
counters, the question's SHA-256, links to the run's `context_requests` and the
cited references. It stores neither the question, the answer nor passage text.
Model transcripts are not stored or logged.

The first provider is deterministic and offline (`offline-extractive-v1`): it
searches once with the question's first distinct words and answers by quoting
the first sentence of each returned passage with its citation. It never calls a
network service. A live model provider is a separate later change: it needs an
SDK dependency, credentials, a decision on what may be sent to the vendor and
evaluation results recorded apart from the reproducible tests.

## Consequences

The model cannot widen its scope: project, employee and agent never appear in
its inputs or in the tool schema, and a request to change them is rejected as
invalid arguments. Instructions inside documents remain data; the offline
provider quotes them, and a compromised provider still cannot reach other
projects or cite passages it was not given. Every returned answer cites at
least one passage that was readable when the response was committed.

Each tool call is a full permission-filtered search, so latency grows with tool
calls and project size. The loop cannot interrupt a provider that blocks; a live
provider must apply its own timeout from `remaining_seconds`. The synchronous
route holds a worker for the whole run. There is no rate limit per agent or
employee yet, and the question digest of a short question can be guessed.
Prior searches stay recorded if a later step fails with a storage error.
