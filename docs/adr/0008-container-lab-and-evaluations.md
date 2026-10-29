# ADR 0008: Container laboratory, independent evaluations and restore drill

- Status: accepted
- Date: 2026-10-29

## Context

Stages 01–07 run from a working copy with `uv run` and a temporary native
PostgreSQL cluster. The final roadmap stage asks for containers of the
application and the training store, documented settings, migrations, backup,
restore and error observation, a set of independent expected results that
detects a deliberately corrupted result, and a full scenario reproducible from a
clean environment. A successful `/health` must not be presented as readiness
for corporate data, and measurements must describe a concrete environment.

## Decision

Build one application image in two stages. The builder copies a digest-pinned
uv 0.12.23 and runs `uv sync --locked --no-dev`; the runtime, based on a
digest-pinned `python:3.12.15-slim`, holds only the virtual environment, `app`,
`migrations` and `alembic.ini`, owned by root and run as UID 10001. Its
healthcheck calls a new `GET /ready`, which reports `ready` only when the
database answers and its Alembic revision equals `SCHEMA_HEAD`; a test keeps
that constant equal to the migration head. `/ready` returns codes, never URLs
or error text, and is not cached. `/health` stays a liveness check.

`compose.yaml` defines `db` (digest-pinned PostgreSQL 17), a one-shot `migrate`
service and `app`. Ports bind to `127.0.0.1` only and `POSTGRES_PASSWORD` is
mandatory. Lab tools from earlier stages keep their loopback `_lab` guard and
run on the host against the published port; the fictional Handbook source stays
on host loopback, so the Stage 07 rule that plain HTTP is loopback-only holds.

`evaluations/cases.json` states 22 externally visible expectations: status and
exact refusal bodies, required and forbidden phrases, allowed and excluded
documents, answer status, required citations, re-reading every reference as
the same agent so `content[start:end]` reproduces the passage, and, for the
offline provider, that each answer line quotes its citation. Cases cover allowed
reads, cross-project and cross-company refusals, an instruction inside a
document, source change, revocation and deletion, a withheld corrupted text, a
read-only mirror and credential and input refusals. `scripts/evaluate.py` holds
pure checks and a stdlib HTTP runner that sends tokens only over HTTPS or
loopback HTTP and ignores proxies.

`scripts/compose_lab.py` runs the whole lab in a disposable project
(`agentic-stage08`) with a password and tokens generated per run and kept in
memory: build, start, seed Alpha and Beta, reject a non-UTF-8 import, sync both
source states, evaluate, then two drills. An audit-failure trigger must yield
`503` without text and without audit rows; a direct permission leak must make
the evaluations fail and pass again after repair. It then dumps with
`pg_dump --format=custom` into ignored `var/backups/`, deletes the volume,
restores with `pg_restore --exit-on-error --no-owner`, and requires equal core
row counts and identical evaluation outcomes. Sequential latency of `/ready`,
context search and answers is recorded with the Docker host description. A new
CI job runs the same scenario on a clean GitHub runner; no image is published.

## Consequences

The scenario proves the lab can be rebuilt, evaluated, broken detectably and
restored from scratch on one machine and in CI. Evaluations are only as strong
as their cases: a defect outside the listed behavior can pass. Drills require
database-owner access and show detection, not prevention. Grounded-quote checks
fit the offline provider only.

The lab connects as the PostgreSQL superuser, keeps secrets in environment
variables, has no TLS, image registry, vulnerability scan, scheduled backups,
alerting or retention policy, and a restored source confirmation keeps aging.
Latency numbers come from a few fictional documents on one laptop and are not
a throughput or capacity claim. A corporate deployment remains separate work.
