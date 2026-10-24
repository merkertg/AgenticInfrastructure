# Testing

[README](../../README.md) · [Verification report](../VERIFICATION.md)

Run from the repository root after installing locked development dependencies:

```bash
uv sync --locked
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy app tests scripts
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

These checks exercise offline policy on supplied objects. They do not establish
authenticated tenant isolation over HTTP, trusted identity, durable audit,
retrieval quality, database behavior or LLM behavior. These are future stages.
The [domain instructions](domain.md) explain the current contract and limits.

For an actual HTTP smoke check, start the development server from
[quickstart](quickstart.md). Expect `200` for `/health`, `/docs`, `/redoc` and
`/openapi.json`; expect `404` for `/`, `/memory` and `/agents`, and `405` for
`POST /health`. A restricted runtime may block local sockets independently of
application behavior; record that restriction instead of reporting a successful
network smoke check.

`scripts/check_docs.py` checks that inline Markdown links resolve to existing
local repository targets. It skips external URLs, anchors and fenced code;
it does not validate external pages or Markdown anchor correctness.
