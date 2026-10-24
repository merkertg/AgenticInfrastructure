# Project structure

[README](../../README.md) · [Approach](../APPROACH.md)

```text
app/
  __init__.py          # application version
  config.py            # validated, cached configuration
  main.py              # health schema, application factory and ASGI entry point
  demo.py              # deterministic fictional offline policy cases
  domain/
    models.py          # validated immutable objects, actions and reason codes
    policy.py          # deterministic access decisions on supplied objects
    audit.py           # audit-attempt model and explicit-time construction
tests/
  conftest.py          # isolated configuration and test client
  test_config.py       # configuration behavior
  test_health.py       # HTTP contract and factory behavior
  domain/              # domain validation, policy and audit-event checks
scripts/
  check_docs.py        # local Markdown target checks
docs/
  STAGE_01.md          # detailed Russian learning guide
  STAGE_02.md          # detailed Russian domain and policy guide
  ROADMAP.md           # stage boundaries and acceptance criteria
  APPROACH.md          # intended product and architecture
  CI_CD.md             # configured checks and delivery limits
  DEVELOPMENT_LOG.md   # development history
  VERIFICATION.md      # observed results and limits
  adr/                # architecture decisions
  instructions/       # short English instructions
  guide/              # verification data and guide index
.github/workflows/ci.yml
.env.example
.python-version
pyproject.toml
uv.lock
AGENTS.md
README.md
```

`app/domain` uses standard Python types and has no web, database, configuration
or model SDK dependencies. The demo composes its objects without importing
`app.main` or reading application settings. Repository, database, authentication,
retrieval and connector packages remain future stages. The application has no
corporate data store or agent runtime.

The runtime path is `client → Uvicorn → FastAPI → health() → HealthResponse → JSON`.
`create_app()` reads validated settings and registers the route. `app` is the
ASGI object imported by Uvicorn. `get_settings()` caches the process configuration.

The offline path is `AccessRequest + supplied objects → evaluate_access() →
AccessDecision → make_audit_event(decision, occurred_at=...)`. It neither returns
document content nor executes a mutation. Actor identifiers in these objects do
not authenticate a user.
