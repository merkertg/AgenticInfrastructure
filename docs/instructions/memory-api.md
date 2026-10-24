# Authenticated memory API

[README](../../README.md) · [Russian Stage 04 guide](../STAGE_04.md) · [ADR](../adr/0004-agent-credentials-memory-api.md)

Stage 04 exposes two memory operations to agents holding a server-issued bearer
credential. The server derives company, employee and agent from the stored
credential and agent registration. Request paths, query parameters, bodies and
other headers never select the actor. This is a learning credential scheme, not
corporate single sign-on.

## Prepare a lab

Use a migrated local lab database ending in `_lab` (see
[persistence usage](persistence.md)), then issue fictional credentials:

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

## Operations

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

## Refusals

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

## Limits

No rate limiting, lockout, failed-login history, credential management API,
document listing or latest-version lookup exists yet. Credential issuance is
privileged lab setup. A stolen token acts as its agent until expiry or
revocation. Search and context assembly belong to Stage 05.
