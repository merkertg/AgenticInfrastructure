# Agentic Infrastructure

A staged Python learning project for shared corporate memory used by agents of
different employees. The target service will return a bounded context with source
references, enforce company and project access for the authenticated employee and
their delegated agent, and record accepted and denied operations.

The project implements the **shared memory** alternative of the
`#AgenticInfrastructure` assignment. Agent discovery, task exchange and a general
corporate automation platform are outside the current roadmap.

**Current implementation: Stage 01 — FastAPI foundation.** The service exposes
`GET /health`, validated settings and generated API documentation. It includes
isolated tests, Ruff, strict mypy, locked dependencies and a GitHub Actions
configuration. Actual check results and publication state belong to
[verification](docs/VERIFICATION.md).

Corporate memory, authentication, permissions, audit storage, document retrieval,
agent tools and corporate connectors are **planned**. Stage 01 needs no database,
model provider, corporate credentials or external service.

## Quickstart

Use Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/).
Run commands from this repository root:

```bash
uv sync --locked
uv run --locked uvicorn app.main:app --reload --host 127.0.0.1 --port 8005
```

In another terminal:

```bash
curl -i http://127.0.0.1:8005/health
```

Default response, HTTP `200`:

```json
{"status":"ok","service":"agentic-infrastructure"}
```

Open [Swagger UI](http://127.0.0.1:8005/docs),
[ReDoc](http://127.0.0.1:8005/redoc) or
[OpenAPI JSON](http://127.0.0.1:8005/openapi.json). `/health` confirms that this
application can respond. It does not check memory availability, authorization or
audit durability. The root path `/` and future business routes return `404`.

Optional settings come from the process environment or a local `.env` based on
[.env.example](.env.example). Defaults are sufficient to start:

| Variable | Default | Meaning |
| --- | --- | --- |
| `APP_NAME` | `agentic-infrastructure` | Nonempty service name; surrounding whitespace is removed |
| `APP_ENV` | `development` | One of `development`, `test`, `production` |
| `APP_DEBUG` | `false` | FastAPI debug mode; keep disabled for normal use |

Explicit `Settings(...)` values take priority over environment variables, then
`.env`, then defaults. Restart the process after changing configuration.
`APP_ENV=production` is a validated label; it does not configure a production
deployment or activate security features.

## Verify

```bash
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy app tests scripts
uv run --locked pytest
uv run --locked python scripts/check_docs.py
```

Tests isolate local settings and require no external services. Passing local
checks, a configured workflow, a published commit and a completed GitHub Actions
run are recorded separately. See [CI](docs/CI_CD.md).

## Target scenario

In fictional company Alpha, a finance assistant may read its employee's finance
documents. An engineering assistant may read approved engineering context but
cannot retrieve finance material. Agents of either employee must also be unable
to read another company's content. A response must respect current permissions,
including revoked grants, and cite the document versions it used.

This scenario is an acceptance target for later stages. Stage 01 stores no
documents and makes no authorization decision.

## Documentation

- [Detailed Stage 01 guide in Russian](docs/STAGE_01.md)
- [Roadmap and completion criteria in Russian](docs/ROADMAP.md)
- [Approach and boundaries](docs/APPROACH.md)
- [Quickstart](docs/instructions/quickstart.md), [API usage](docs/instructions/usage.md),
  [testing](docs/instructions/testing.md), [project structure](docs/instructions/project-structure.md)
- [CI and publication](docs/CI_CD.md)
- [Architecture decision](docs/adr/0001-shared-memory-and-staged-delivery.md)
- [Development log](docs/DEVELOPMENT_LOG.md) and [verification](docs/VERIFICATION.md)
- [Learning guide index](docs/guide/README.md)

New layers, services and dependencies are added with the stage that needs them.
The roadmap describes intended behavior; the verification report describes what
has actually been checked.
