# Testing

[README](../../README.md) · [Verification report](../VERIFICATION.md)

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
documentation endpoints and absent business routes. Domain tests also cover
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
The [domain instructions](domain.md) explain the offline contract. Database
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
requests and rollback on audit failure. The [persistence guide](persistence.md)
describes local setup.

Neither suite establishes authenticated identity over HTTP, retrieval quality,
corporate source behavior or LLM behavior. A process reconnection and a real
database-server restart are different checks; the verification report states
which was actually performed.

For an actual HTTP smoke check, start the development server from
[quickstart](quickstart.md). Expect `200` for `/health`, `/docs`, `/redoc` and
`/openapi.json`; expect `404` for `/`, `/memory` and `/agents`, and `405` for
`POST /health`. A restricted runtime may block local sockets independently of
application behavior; record that restriction instead of reporting a successful
network smoke check.

`scripts/check_docs.py` checks that inline Markdown links resolve to existing
local repository targets. It skips external URLs, anchors and fenced code;
it does not validate external pages or Markdown anchor correctness.
