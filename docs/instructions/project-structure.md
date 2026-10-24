# Project structure

[README](../../README.md) · [Approach](../APPROACH.md)

```text
app/
  __init__.py          # application version
  config.py            # validated, cached configuration
  main.py              # health schema, application factory and ASGI entry point
tests/
  conftest.py          # isolated configuration and test client
  test_config.py       # configuration behavior
  test_health.py       # HTTP contract and factory behavior
scripts/
  check_docs.py        # local Markdown target checks
docs/
  STAGE_01.md          # detailed Russian learning guide
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

No domain, repository, database, authentication, retrieval or connector packages
are created before their stages. The application currently has no corporate
data store or agent runtime.

The runtime path is `client → Uvicorn → FastAPI → health() → HealthResponse → JSON`.
`create_app()` reads validated settings and registers the route. `app` is the
ASGI object imported by Uvicorn. `get_settings()` caches the process configuration.
