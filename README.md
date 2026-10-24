# Agentic Infrastructure

A staged Python learning project for shared corporate memory used by agents of
different employees. The target service will return a bounded context with source
references, enforce company and project access for the authenticated employee and
their delegated agent, and record accepted and denied operations.

The project implements the **shared memory** alternative of the
`#AgenticInfrastructure` assignment. Agent discovery, task exchange and a general
corporate automation platform are outside the current roadmap.

**Current implementation: Stages 01–03 — API foundation, access policy and
PostgreSQL persistence.** The HTTP service still exposes only `GET /health`
and generated documentation. The pure Python domain models companies,
employees, delegated agents, documents, versions and grants. A separate storage
adapter reads existing versions, appends new versions and commits operation
audit metadata and write-replay records transactionally. Actual check results
and publication state belong to [verification](docs/VERIFICATION.md).

Persistence operations currently use a trusted Python caller and fictional
laboratory data. Authenticated identity, an authorized memory HTTP API, search,
agent tools and corporate connectors are **planned**. The health service and
offline policy demo need no database; the persistence demo and integration tests
need PostgreSQL. No model provider or corporate credentials are required.

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

## PostgreSQL laboratory

Use a separate local laboratory database and apply the schema before running
the persistence demo:

```bash
export DATABASE_URL='postgresql+psycopg://agentic@127.0.0.1:55435/agentic_lab'
uv run --locked alembic upgrade head
uv run --locked python -m app.persistence_demo --setup
uv run --locked python -m app.persistence_demo
```

Reads use current stored permissions and return content only after audit commit.
Writes append a new version with an expected current revision number. The new
version, audit row and idempotency row commit together. Identical authorized
retries reuse the original result; changed payloads and stale versions conflict.
Access is reevaluated before replay. Write results contain version metadata,
not document content.

Run `--setup` once in a new local database ending in `_lab`; it creates fictional
starting records and rejects an already populated company set. Further demo runs
use the saved data and replay the same write. The lab's actor IDs are still
metadata, not authenticated identity. No memory
route is added to FastAPI. See [persistence instructions](docs/instructions/persistence.md)
and the [detailed Stage 03 guide](docs/STAGE_03.md) for database setup, test safety,
transaction boundaries and current limitations.

## Verify

```bash
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy app tests scripts migrations
uv run --locked pytest -m 'not postgres'
uv run --locked python scripts/check_docs.py
```

Unit tests isolate local settings and require no external services. PostgreSQL
tests require an explicit disposable local test database:

```bash
export TEST_DATABASE_URL='postgresql+psycopg://agentic@127.0.0.1:55435/agentic_test'
uv run --locked pytest -m postgres
```

An absent test URL skips database tests in ordinary runs; it does not verify
persistence. Passing local checks, a configured workflow, a published commit and
a completed GitHub Actions run are recorded separately. See [CI](docs/CI_CD.md).

## Target scenario

In fictional company Alpha, a finance assistant may read its employee's finance
documents. An engineering assistant may read approved engineering context but
cannot retrieve finance material. Agents of either employee must also be unable
to read another company's content. A response must respect current permissions,
including revoked grants, and cite the document versions it used.

Stage 02 checks policy on immutable in-memory objects. Stage 03 loads stored
policy objects and preserves version appends and audit history in PostgreSQL.
Search, source synchronization and authenticated HTTP handling remain future
stages.

## Documentation

- [Detailed Stage 01 guide in Russian](docs/STAGE_01.md)
- [Detailed Stage 02 guide in Russian](docs/STAGE_02.md)
- [Detailed Stage 03 guide in Russian](docs/STAGE_03.md)
- [Roadmap and completion criteria in Russian](docs/ROADMAP.md)
- [Approach and boundaries](docs/APPROACH.md)
- [Quickstart](docs/instructions/quickstart.md), [API usage](docs/instructions/usage.md),
  [testing](docs/instructions/testing.md), [project structure](docs/instructions/project-structure.md)
- [CI and publication](docs/CI_CD.md)
- [Architecture decision](docs/adr/0001-shared-memory-and-staged-delivery.md)
- [Domain policy decision](docs/adr/0002-domain-memory-access-policy.md)
- [Persistence instructions](docs/instructions/persistence.md) and
  [transaction decision](docs/adr/0003-postgresql-memory-audit.md)
- [Development log](docs/DEVELOPMENT_LOG.md) and [verification](docs/VERIFICATION.md)
- [Learning guide index](docs/guide/README.md)

New layers, services and dependencies are added with the stage that needs them.
The roadmap describes intended behavior; the verification report describes what
has actually been checked.
