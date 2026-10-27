# Project structure

[README](../../README.md) · [Approach](../APPROACH.md)

```text
app/
  __init__.py          # application version
  config.py            # validated, cached configuration
  main.py              # health schema, application factory and ASGI entry point
  demo.py              # deterministic fictional offline policy cases
  persistence_demo.py  # fictional PostgreSQL scenario
  credential_lab.py    # guarded issuance of fictional lab agent credentials
  intake_lab.py        # guarded all-or-nothing import of fictional text files
  api/
    memory.py          # authenticated memory and context routes and schemas
  domain/
    models.py          # validated immutable objects, actions and reason codes
    policy.py          # deterministic access decisions on supplied objects
    audit.py           # audit-attempt model and explicit-time construction
    text.py            # intake text format and located fragments
    retrieval.py       # query rules, BM25 ranking and context budgets
    versions.py        # deterministic version identifiers
  application/
    memory.py          # result contract for memory operations
    identity.py        # authenticated principal derived from a credential
    context.py         # context-search result contract
  persistence/
    database.py        # PostgreSQL engine and migration helpers
    schema.py          # SQLAlchemy Core tables and relational constraints
    memory.py          # transactional reads, appends, audit and write replay
    credentials.py     # hashed bearer credentials, authentication and recheck
    context.py         # authorized candidate loading and context history
    intake.py          # validated all-or-nothing document intake
    lab.py             # guarded fictional bootstrap for a local lab
tests/
  conftest.py          # isolated configuration and test client
  test_config.py       # configuration behavior
  test_health.py       # HTTP contract and factory behavior
  test_memory_api.py   # memory API checks that need no database
  domain/              # domain validation, policy and audit-event checks
  persistence/         # real PostgreSQL migration, credential and HTTP checks
migrations/
  env.py               # Alembic migration environment
  versions/            # versioned schema changes
alembic.ini
fixtures/lab/          # invented UTF-8 texts for the intake and search lab
scripts/
  check_docs.py        # local Markdown target checks
docs/
  STAGE_01.md          # detailed Russian learning guide
  STAGE_02.md          # detailed Russian domain and policy guide
  STAGE_03.md          # detailed Russian persistence and transaction guide
  STAGE_04.md          # detailed Russian identity and memory API guide
  STAGE_05.md          # detailed Russian intake and context search guide
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
`app.main` or reading application settings. PostgreSQL persistence can be called
by trusted Python code or by the authenticated HTTP routes. Retrieval, agent
runtime and connectors remain future stages.

The liveness path is `client → Uvicorn → FastAPI → health() → HealthResponse → JSON`.
`create_app()` reads validated settings, builds a lazy engine when
`APP_DATABASE_URL` is set and registers the routes. `app` is the ASGI object
imported by Uvicorn. `get_settings()` caches the process configuration.

The memory HTTP path is `Authorization: Bearer → CredentialAuthenticator →
Principal → AccessRequest → MemoryStore (credential recheck under the company
lock) → evaluate_access() → audit → commit → JSON`. Denials return one uniform
`404`; storage and audit failures return `503`.

The context path is `Principal → ScopeRequest → MemoryStore.search_context()
(company lock, credential recheck, evaluate_scope(), readable versions,
fragments, BM25, final evaluate_access()) → context history → commit → JSON`.

The offline path is `AccessRequest + supplied objects → evaluate_access() →
AccessDecision → make_audit_event(decision, occurred_at=...)`. It neither returns
document content nor executes a mutation. Actor identifiers in these objects do
not authenticate a user.

The persistence path is `AccessRequest → MemoryStore → transaction/company lock
→ stored objects → evaluate_access() → read/append + audit → commit → result`.
A write also stores its idempotency fingerprint and original result. The adapter
loads current grants before replay; it does not treat an old result as authority.
