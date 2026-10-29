# Agentic Infrastructure

A staged Python learning project for shared corporate memory used by agents of
employees. It implements the shared-memory alternative of the
`#AgenticInfrastructure` assignment. All companies, employees and documents are
fictional laboratory data.

**Stages 01–06 are implemented:** FastAPI, deterministic access policy,
PostgreSQL version storage and audit, hashed bearer credentials, trusted text
intake, permission-filtered BM25 context search, and a bounded agent that
answers through one server-scoped memory tool with citation checks. The server
derives the employee from its registered agent and checks current employee
rights and agent delegation before reading content and again before returning
an answer. Reads, writes, searches and agent runs require transactional audit.
The model provider is a deterministic offline one; a live model, corporate
connectors and container delivery remain planned. Actual checks and publication
evidence are recorded separately in [verification](docs/VERIFICATION.md).

Start with the [quickstart](docs/instructions/quickstart.md). The health service
and offline domain demo need no database or credentials. Memory routes and the
laboratory need PostgreSQL and server-issued fictional agent credentials.

## Roadmap

| Stage | Work | Status |
| --- | --- | --- |
| 01 | FastAPI, settings, health endpoint and isolated tests | Implemented |
| 02 | Pure Python memory models and employee/agent access policy | Implemented |
| 03 | PostgreSQL versions, transactions, audit and write replay | Implemented |
| 04 | Hashed agent credentials and authenticated memory API | Implemented |
| 05 | Text intake, permission-filtered BM25 and verifiable context references | Implemented |
| 06 | Bounded context tool for an agent and offline model provider | Implemented |
| 07 | One corporate source with read-only synchronization | Planned |
| 08 | Containers, evaluations, backup and release | Planned |

See the [detailed roadmap and completion criteria](docs/ROADMAP.md).

## Documentation

Short English instructions ([docs/instructions/](docs/instructions/)):

- [Quickstart](docs/instructions/quickstart.md): requirements, local startup and the PostgreSQL laboratory
- [Usage](docs/instructions/usage.md): settings, offline policy, persistence, credentials, version API, document intake, context search and agent answers
- [Testing and verification](docs/instructions/testing.md): unit tests, isolated PostgreSQL tests, lint, types and documentation checks
- [CI and publication](docs/instructions/ci-and-delivery.md): workflow, local checks and safe publication
- [Project structure](docs/instructions/project-structure.md): current modules and request flow

Detailed learning guides in Russian:

- [Stage 01: API foundation](docs/STAGE_01.md)
- [Stage 02: domain and access policy](docs/STAGE_02.md)
- [Stage 03: PostgreSQL, versions and audit](docs/STAGE_03.md)
- [Stage 04: agent identity and memory API](docs/STAGE_04.md)
- [Stage 05: document intake and context search](docs/STAGE_05.md)
- [Stage 06: bounded agent context tool](docs/STAGE_06.md)
- [CI/CD guide](docs/CI_CD.md)
- [Illustrated PDF guide](docs/guide/agentic-infrastructure-guide.pdf) and [rebuild instructions](docs/guide/README.md)
- [Verification report](docs/VERIFICATION.md) and [machine-readable evidence](docs/guide/verification.json)

Design and history:

- [Solution approach](docs/APPROACH.md)
- [Shared memory and staged delivery](docs/adr/0001-shared-memory-and-staged-delivery.md)
- [Domain access policy](docs/adr/0002-domain-memory-access-policy.md)
- [PostgreSQL transactions and audit](docs/adr/0003-postgresql-memory-audit.md)
- [Agent credentials](docs/adr/0004-agent-credentials-memory-api.md)
- [Permission-filtered context](docs/adr/0005-permission-filtered-context.md)
- [Bounded agent context tool](docs/adr/0006-bounded-agent-context-tool.md)
- [Development log](docs/DEVELOPMENT_LOG.md)
