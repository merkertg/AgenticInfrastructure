# ADR 0005: Bounded intake and permission-filtered context retrieval

- Status: accepted
- Date: 2026-10-27

## Context

Stage 04 lets an authenticated agent read a version whose ID it already knows.
Agents need to find relevant passages instead. Retrieval is where access leaks
usually happen: unauthorized text can reach snippets, counts, ranking
statistics, error messages or caches. The service also needs documents to search,
accepted in a predictable format with explicit limits.

## Decision

Accept one intake format: UTF-8 plain text, at most 64 KiB and 20,000 characters
after decoding. A leading byte-order mark is removed and line endings become
`\n`. Invalid UTF-8, empty text and control characters other than tab and
newline are rejected with explicit reason codes. Intake is trusted laboratory
setup: it creates documents with version 1 and optional employee grants,
validates the whole batch (at most 50 files) before writing and imports nothing
when any item fails. A project holds at most 500 documents. Agents still extend
existing documents only through the Stage 04 append route.

Split versions into fragments at blank lines; paragraphs longer than 800
characters are cut at whitespace. Each fragment carries its ordinal and start/end
offsets in Unicode code points, so `content[start:end]` reproduces the text.
Fragments are computed from the current version when a search runs. No derived
index or result cache is stored, so neither can become stale or bypass a later
revocation; this trades performance for consistency at learning scale.

`POST /memory/projects/{project_id}/context` takes a query of at most 256
characters and 16 distinct terms, and `max_items` from 1 to 10. In one
`READ COMMITTED` transaction holding the company row lock, the store:

1. rechecks the credential and the project scope (company, project, employee,
   agent and read delegation) through pure domain rules;
2. selects active documents of the project and their latest active version;
3. evaluates `evaluate_access()` for each and keeps only authorized versions,
   at most 200 documents ordered by ID (`truncated` reports a cut);
4. loads content of authorized versions only, splits and ranks them with BM25
   whose term statistics come only from those authorized fragments;
5. selects the best fragments within 10 items and 4,000 characters;
6. reevaluates access for every selected document and drops any that lost it;
7. stores a context request and its returned references, then commits.

Ties are ordered by document ID, version number and fragment ordinal, so equal
input gives equal output. Vector search is not used; it would need a separate
decision and evidence that lexical ranking is insufficient.

A project-scope denial returns the uniform `404` of the memory API. An allowed
search without matching authorized evidence returns `200` with status
`no_authorized_evidence` and no items. Responses contain no totals of
unauthorized material. Context audit stores the query's SHA-256 and term count,
not its text, plus the version and fragment references returned, not content.

## Consequences

Unauthorized documents cannot affect fragments, scores, counts or errors,
because their text is never loaded during a search. Revocation, document
deactivation and new versions apply to the next request immediately. Each search
rereads and splits candidate versions, so latency grows with project size; the
200-document cut keeps that bounded. Query hashes of short queries can be guessed
and are correlation aids, not secrets. Better ranking, persisted indexes, caches
and semantic search need their own decisions with permission-aware invalidation.
