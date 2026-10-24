# Agentic Infrastructure

A staged Python learning project for shared corporate memory used by agents of
different employees. The target service will return a bounded context with source
references, enforce company and project access for the authenticated employee and
their delegated agent, and record accepted and denied operations.

The project implements the **shared memory** alternative of the
`#AgenticInfrastructure` assignment. Agent discovery, task exchange and a general
corporate automation platform are outside the current roadmap.

**Current implementation: Stages 01–02 — API foundation and offline access
policy.** The HTTP service exposes `GET /health`, validated settings and generated
API documentation. A separate pure Python domain models companies, employees,
delegated agents, documents, versions and grants. It evaluates access requests
and constructs audit-event metadata on fictional inputs. Actual check results
and publication state belong to [verification](docs/VERIFICATION.md).

Durable memory, authenticated identity, HTTP authorization, audit storage,
document retrieval, agent tools and corporate connectors are **planned**. The
current build needs no database, model provider, corporate credentials or
external service.

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

## Offline domain demo

```bash
uv run --locked python -m app.demo
```

The deterministic demo evaluates fictional finance and engineering requests.
Employee document rights and an active agent delegation must both permit the
requested action in the same company and project. Missing or revoked access is
denied, and `read` and `write` remain separate actions. The demo returns decisions
and audit-attempt metadata; it does not return document content, change memory or
persist history. It requires no server or `.env`.

An `employee_id` in these offline inputs is metadata, not proof of identity.
See the [domain instructions](docs/instructions/domain.md) and
[detailed Stage 02 guide](docs/STAGE_02.md).

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

Stage 02 checks the access-policy part of this scenario on immutable in-memory
objects. Source retrieval, storage and authenticated request handling remain
future stages.

## Documentation

- [Detailed Stage 01 guide in Russian](docs/STAGE_01.md)
- [Detailed Stage 02 guide in Russian](docs/STAGE_02.md)
- [Roadmap and completion criteria in Russian](docs/ROADMAP.md)
- [Approach and boundaries](docs/APPROACH.md)
- [Quickstart](docs/instructions/quickstart.md), [API usage](docs/instructions/usage.md),
  [testing](docs/instructions/testing.md), [project structure](docs/instructions/project-structure.md)
- [CI and publication](docs/CI_CD.md)
- [Architecture decision](docs/adr/0001-shared-memory-and-staged-delivery.md)
- [Domain policy decision](docs/adr/0002-domain-memory-access-policy.md)
- [Development log](docs/DEVELOPMENT_LOG.md) and [verification](docs/VERIFICATION.md)
- [Learning guide index](docs/guide/README.md)

New layers, services and dependencies are added with the stage that needs them.
The roadmap describes intended behavior; the verification report describes what
has actually been checked.
