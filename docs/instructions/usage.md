# Current API and offline demo

[README](../../README.md) · [Quickstart](quickstart.md)

The HTTP API still exposes one application operation:

| Method | Path | Result |
| --- | --- | --- |
| GET | `/health` | `200`, `{"status":"ok","service":"agentic-infrastructure"}` with default settings |

This is liveness only: the current HTTP application can answer a request.
It performs no database, identity, connector, memory, authorization or LLM probe.
It does not record an audit event. The Stage 02 policy and audit-event model are
separate offline code and are not called by the health handler.

`POST /health` returns `405`. `/`, `/memory` and `/agents` return `404`.
`/docs`, `/redoc` and `/openapi.json` provide generated API documentation.

| Setting | Default | Meaning |
| --- | --- | --- |
| `APP_NAME` | `agentic-infrastructure` | Service title and health response name; whitespace is trimmed and empty names are rejected |
| `APP_ENV` | `development` | Validated label: `development`, `test` or `production` |
| `APP_DEBUG` | `false` | Explicit local traceback/debug option |

Configuration precedence: explicit `Settings(...)` values, process environment,
local `.env`, defaults. Settings are cached by `get_settings()` for the process.
Restart the server after changes. `create_app(settings)` allows tests to inject a
separate configuration. `APP_ENV` does not change permissions or install an
identity provider; leave debug disabled when handling any sensitive information.

The [roadmap](../ROADMAP.md) describes future shared-memory behavior. No current
HTTP request can read or write corporate context.

For the Stage 02 access-policy demonstration, run from the repository root:

```bash
uv run --locked python -m app.demo
```

The demo evaluates fictional authorization attempts and constructs audit-event
metadata in memory. It needs no server, environment settings, corporate data,
database or model. It performs no content read/write or durable audit recording.
See [domain usage](domain.md) and the [Russian guide](../STAGE_02.md).
