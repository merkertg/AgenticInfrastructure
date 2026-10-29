# Testing

[← README](../../README.md) · [All documentation](../../README.md#documentation) · [Verification report](../VERIFICATION.md)

Run from the repository root after installing locked development dependencies:

```bash
uv sync --locked
uv run --locked pytest -m 'not postgres'
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy app tests scripts migrations
uv run --locked python scripts/check_docs.py
uv lock --check --offline
git diff --check
```

Tests cover settings defaults, `.env` and environment precedence, validation,
caching, independent application factories, the health JSON contract, OpenAPI,
documentation endpoints, absent routes, bearer challenge responses, token shape,
memory request bodies and lab credential guards. Domain tests also cover
model validation and immutability, allowed and denied policy decisions, action
separation, cross-company/project relationships, revoked grants/delegation and
audit-attempt metadata with an explicit timestamp. Each test runs in a temporary
directory with `APP_*` environment variables removed and the settings cache reset.
The HTTP test client calls the ASGI application directly without a socket.

Run only the domain tests or the deterministic demo with:

```bash
uv run --locked pytest tests/domain
uv run --locked python -m app.demo
```

These checks exercise the HTTP foundation, offline policy and input validation.
The [domain instructions](usage.md#offline-domain-and-access-policy) explain the offline contract. Database
behavior requires a separate actual PostgreSQL run:

```bash
export TEST_DATABASE_URL='postgresql+psycopg://agentic@127.0.0.1:55435/agentic_test'
uv run --locked pytest -m postgres
```

The persistence fixture accepts only a local PostgreSQL URL and a disposable
database name ending in `_test` before resetting tables. It does not fall back to
`DATABASE_URL`. Missing `TEST_DATABASE_URL` skips these tests in ordinary runs;
a configured but unavailable database fails. The tests exercise real migrations,
stored versions, company/project constraints, write replay, conflicts, concurrent
requests and rollback on audit failure. They also issue hashed credentials and
call the memory HTTP routes through `TestClient` on the same database: invalid,
revoked and expired credentials, spoofed identity headers and body fields,
other companies and projects, revoked delegation, exceeded rights, write replay
and conflicts, audit failure and revocation between authentication and the
locked operation. Stage 05 adds all-or-nothing intake, permission filtering
before ranking, score independence from unreadable documents, explicit empty
results, revocation and deletion between searches, new and inactive versions,
other projects and companies, embedded instructions, revocation after ranking,
search limits and context-history failure. Stage 06 adds agents of different
employees in one project, scope denial without starting the model, revocation
between search and answer, revoked delegation and credential during a run,
embedded instructions, run-history failure and the answer route's `200`, `404`,
`422`, `502` and `504` responses. Domain tests cover the text format,
fragment offsets, query rules, deterministic ranking, project scope and tool
argument, question and answer rules. Agent-loop tests use a scripted provider,
a fake search and a fake clock for limits, provider errors, malformed turns and
invented citations. The [persistence guide](usage.md#postgresql-persistence) describes local setup.

Neither suite establishes production identity integration, semantic retrieval
quality, real corporate source behavior or live LLM behavior: the agent tests use the
offline provider and scripted fakes only. A process reconnection and a real
database-server restart are different checks; the verification report states
which was actually performed.

For an actual HTTP smoke check, start the development server from
[quickstart](quickstart.md). Expect `200` for `/health`, `/docs`, `/redoc` and
`/openapi.json`; expect `404` for `/`, `/memory` and `/agents`, and `405` for
`POST /health`. For memory routes, follow [memory API usage](usage.md#authenticated-memory-api)
with a lab database and issued credentials. A restricted runtime may block local sockets independently of
application behavior; record that restriction instead of reporting a successful
network smoke check.

`scripts/check_docs.py` checks that inline Markdown links resolve to existing
local repository targets. It skips external URLs, anchors and fenced code;
it does not validate external pages or Markdown anchor correctness.

## Illustrated guide

Guide-data tests run with the ordinary suite and need no ReportLab. To rebuild
the PDF, explicitly install the optional guide group:

```bash
uv sync --locked --group guide
uv run --locked --group guide python -m scripts.build_guide
```

Inspect rendered pages as described in [the guide index](../guide/README.md).
The PDF prints the current stage checks recorded in `docs/guide/verification.json`;
skipped database tests never count as a database verification.


## Source synchronization checks

Source/domain and collector tests use fictional JSON and fake clients. The
Handbook adapter tests use loopback HTTP with a temporary in-memory token; no
external service or developer `.env` is needed. PostgreSQL tests cover complete
sync, replay, content changes, deletion/restoration, mapped read grants,
read-only writes, unavailable/expired sources, source filtering before ranking,
final answer checks, rollback on history failure, schema constraints and
competing snapshots. Run the full suites above; targeted checks are:

```bash
uv run --locked pytest tests/domain/test_source.py tests/test_source_sync.py tests/test_handbook_client.py tests/test_source_cli.py
uv run --locked pytest tests/persistence/test_sources.py
```

The second command needs the explicit validated `TEST_DATABASE_URL`. A successful
loopback test proves the fictional HTTP contract only; it does not prove real
corporate credentials, ACL interpretation or production connectivity.
