# Project structure

[← README](../../README.md) · [All documentation](../../README.md#documentation) · [Approach](../APPROACH.md)

```text
app/
  __init__.py          # application version
  config.py            # validated, cached configuration and secret-file reader
  main.py              # health and readiness, application factory and ASGI entry point
  demo.py              # deterministic fictional offline policy cases
  persistence_demo.py  # fictional PostgreSQL scenario
  credential_lab.py    # guarded issuance of fictional lab agent credentials
  intake_lab.py        # guarded all-or-nothing import of fictional text files
  source_lab.py        # loopback fictional Handbook HTTP source
  source_sync.py       # guarded source registration, synchronization and status
  connectors/
    handbook.py        # fixed-host GET adapter without redirects or proxies
  api/
    memory.py          # authenticated memory and context routes and schemas
    answers.py         # bounded agent answer route and schemas
  domain/
    models.py          # validated immutable objects, actions and reason codes
    policy.py          # deterministic access decisions on supplied objects
    audit.py           # audit-attempt model and explicit-time construction
    text.py            # intake text format and located fragments
    retrieval.py       # query rules, BM25 ranking and context budgets
    versions.py        # deterministic version identifiers
    tool.py            # memory tool schema, argument, question and answer rules
    strict_json.py     # shared duplicate-key and non-finite JSON rejection
    source.py          # source contract, freshness and pure snapshot planning
  application/
    memory.py          # result contract for memory operations
    identity.py        # authenticated principal derived from a credential
    context.py         # context-search result contract
    agent.py           # provider contract, run limits and the bounded agent loop
    offline_model.py   # deterministic offline provider that quotes passages
    source_sync.py     # bounded full snapshot collection and sync result contracts
  persistence/
    database.py        # PostgreSQL engine and migration helpers
    schema.py          # SQLAlchemy Core tables and relational constraints
    memory.py          # transactional reads, appends, audit and write replay
    credentials.py     # hashed bearer credentials, authentication and recheck
    context.py         # authorized candidate loading and context history
    intake.py          # validated all-or-nothing document intake
    runs.py            # agent run history without question or answer text
    sources.py         # registration, atomic sync, grants and source-access gates
    lab.py             # guarded fictional bootstrap of Alpha and Beta for a local lab
tests/
  conftest.py          # isolated configuration and test client
  test_config.py       # configuration behavior
  test_health.py       # HTTP contract and factory behavior
  test_memory_api.py   # memory API checks that need no database
  test_agent_runtime.py # agent loop and offline provider with scripted fakes
  test_answer_api.py   # answer route checks that need no database
  test_evaluate.py     # evaluation checks against corrupted responses
  test_compose_lab.py  # container scenario guards without Docker
  domain/              # domain validation, policy and audit-event checks
  persistence/         # real PostgreSQL migration, credential and HTTP checks
migrations/
  env.py               # Alembic migration environment
  versions/            # versioned schema changes
alembic.ini
fixtures/lab/          # invented UTF-8 texts of Alpha and Beta for the labs
fixtures/source/       # two fictional Handbook snapshots
evaluations/cases.json # independent expected results of the container lab
scripts/
  check_docs.py        # local Markdown target checks
  guide_data.py        # validated verification facts, independent of ReportLab
  build_guide.py       # explicit CLI for the illustrated Russian PDF guide
  evaluate.py          # HTTP evaluation runner and pure response checks
  compose_lab.py       # full disposable container scenario with drills and restore
  lab_secrets.py       # private secret files for the Compose lab
docs/
  STAGE_01.md          # detailed Russian learning guide
  STAGE_02.md          # detailed Russian domain and policy guide
  STAGE_03.md          # detailed Russian persistence and transaction guide
  STAGE_04.md          # detailed Russian identity and memory API guide
  STAGE_05.md          # detailed Russian intake and context search guide
  STAGE_06.md          # detailed Russian agent context tool guide
  STAGE_07.md          # detailed Russian source synchronization guide
  STAGE_08.md          # detailed Russian container, evaluation and restore guide
  STAGE_09.md          # detailed Russian least-privilege release guide
  ROADMAP.md           # stage boundaries and acceptance criteria
  APPROACH.md          # intended product and architecture
  CI_CD.md             # configured checks and delivery limits
  DEVELOPMENT_LOG.md   # development history
  VERIFICATION.md      # observed results and limits
  adr/                # architecture decisions
  instructions/       # short English instructions
  guide/              # illustrated PDF, verification data and rebuild instructions
.github/workflows/ci.yml # checks job and container-lab job
Dockerfile             # two-stage, digest-pinned, non-root application image
.dockerignore
compose.yaml           # db, one-shot migrate and app; file secrets, locked down
docker/postgres/       # database image: pinned PostgreSQL, role script, no gosu
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
by trusted Python code or by the authenticated HTTP routes. Permission-filtered
lexical retrieval and a bounded agent loop with an offline provider are
implemented, along with one read-only source adapter and a fictional HTTP lab;
a live model provider and a real corporate connection remain future work.
`app/application/agent.py` imports neither FastAPI nor SQLAlchemy: its search
tool is a callable that the HTTP route binds to one agent and project.

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

The answer path is `Principal → MemoryStore.check_scope() → run_agent()
(provider step → parse_search_call() → search_context() per call → citation
check) → MemoryStore.finish_run() (company lock, scope and cited-version
recheck, run history) → commit → JSON`. Denials return the uniform `404`,
failed runs `502` or `504` with an outcome code only.

The offline path is `AccessRequest + supplied objects → evaluate_access() →
AccessDecision → make_audit_event(decision, occurred_at=...)`. It neither returns
document content nor executes a mutation. Actor identifiers in these objects do
not authenticate a user.

The persistence path is `AccessRequest → MemoryStore → transaction/company lock
→ stored objects → evaluate_access() → read/append + audit → commit → result`.
A write also stores its idempotency fingerprint and original result. The adapter
loads current grants before replay; it does not treat an old result as authority.


The source path is `trusted source settings → HandbookClient → collect_snapshot()
→ apply_snapshot() (company lock, current settings/account recheck, plan_sync(),
versions, grants, confirmation and run history) → commit`. Fetching holds no
company lock. Search filters expired sources in SQL before loading text;
`source_denials()` also protects direct version reads, appends and final answer
checks. A synchronization does not create employee identity or agent delegation.

The container path is `compose up → db healthy → migrate as the schema owner
(alembic upgrade head) → app as agentic_app → /ready`. Lab tools, the fictional source and evaluations run on the host
against loopback ports; `scripts/compose_lab.py` composes them into one
reproducible scenario.
