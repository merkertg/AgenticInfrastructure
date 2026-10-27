# Document intake and context search

[README](../../README.md) · [Russian Stage 05 guide](../STAGE_05.md) · [ADR](../adr/0005-permission-filtered-context.md)

Stage 05 imports fictional UTF-8 text documents through trusted lab setup and
lets authenticated agents search the documents they may currently read.

## Import documents

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

## Search

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

## Guarantees and limits

The project scope and each document are authorized before ranking; text of
unreadable documents is not loaded and does not influence BM25 statistics.
Selected documents are authorized again before the response. Fragments are
computed per request, and no index or result cache is stored, so revocation,
deactivation and new versions apply immediately. Search uses the latest active
version of each document and at most 200 readable documents ordered by ID.

`context_requests` stores scope, decision, result, counts and the query's
SHA-256, not the query text; `context_items` stores returned version and
fragment references, not text. Lexical ranking has no synonyms or semantics.
