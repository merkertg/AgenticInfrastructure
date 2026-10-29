# Quickstart

[← README](../../README.md) · [All documentation](../../README.md#documentation) · [Detailed Russian guide](../STAGE_01.md)

Use Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/).
Run commands from the repository root:

```bash
uv sync --locked
cp -n .env.example .env
uv run --locked uvicorn app.main:app --reload --host 127.0.0.1 --port 8005
```

The `.env` copy is optional: defaults allow local startup. Do not commit `.env`.
`--reload` is for development. Bind to loopback while using this learning stage.
`APP_ENV=production` is a configuration label; it does not configure corporate identity, TLS
or a production deployment.

In another terminal:

```bash
curl -i http://127.0.0.1:8005/health
```

Expected status: `200`. Default body:

```json
{"status":"ok","service":"agentic-infrastructure"}
```

Open [Swagger UI](http://127.0.0.1:8005/docs),
[ReDoc](http://127.0.0.1:8005/redoc) or
[OpenAPI JSON](http://127.0.0.1:8005/openapi.json) while the server is running.
Swagger UI and ReDoc load browser assets from a CDN; their HTML endpoints can
respond even when the browser cannot load those assets offline.

The root `/`, `/memory` and `/agents` return `404`. Memory routes under
`/memory/projects/...` need a lab database and a bearer credential; without
`APP_DATABASE_URL` they return `503`. See [memory API usage](usage.md#authenticated-memory-api).
Stop the server with `Ctrl+C`.

If uv cannot write to its cache, use a writable cache directory for the command:

```bash
UV_CACHE_DIR=/private/tmp/agentic-infrastructure-uv-cache uv sync --locked
```

If package downloads fail, check network access. If port 8005 is occupied, choose
another local port. Neither issue is evidence that the health handler is wrong.

## PostgreSQL laboratory

Create an isolated local PostgreSQL database, then follow
[the persistence setup](usage.md#postgresql-persistence),
[issue fictional agent credentials](usage.md#authenticated-memory-api),
[import and search documents](usage.md#document-intake-and-context-search) and
[ask the offline agent](usage.md#agent-answers) and
[synchronize the fictional source](usage.md#read-only-source-synchronization).
The [Stage 03 guide](../STAGE_03.md) includes native PostgreSQL setup.
Use separate databases ending in `_lab` for the lab and `_test` for tests.

For the database-free policy demonstration:

```bash
uv run --locked python -m app.demo
```
