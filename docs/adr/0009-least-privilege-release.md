# ADR 0009: Least-privilege database role, file secrets and image scanning

- Status: accepted
- Date: 2026-10-30

## Context

The Stage 08 laboratory listed its own weaknesses: the API connected to
PostgreSQL as the superuser that owns the schema, the password reached
containers as an environment variable, containers had writable root file
systems and default capabilities, and no image was checked for known
vulnerabilities. A compromised API process could have granted itself access,
rewritten versions or erased the audit trail.

## Decision

Migration `0006_app_role` creates the cluster-wide group role
`agentic_memory_app` if it is missing and grants exactly what the HTTP service
uses: `SELECT` on policy, credential and source-state tables; `SELECT, INSERT`
on `versions` and `write_requests`; `INSERT` only on audit, context and run
history; nothing on source account mappings and sync runs. Because
`SELECT ... FOR UPDATE` requires an update privilege, the group holds
`UPDATE (name)` on `companies`, and a trigger rejects any update of
`companies` by a non-superuser member with `insufficient_privilege`. Downgrade
removes this database's grants and the trigger but keeps the role, which other
databases may use. A test compares every table's privileges with a documented
matrix, so each future table needs an explicit decision.

The API connects as the login role `agentic_app`, a member of the group,
created once per volume by an init script baked into a small database image.
Migrations, lab tools and source synchronization keep the owner role.

Secrets are Docker Compose file secrets written by `scripts/lab_secrets.py`:
owner password, service password, the migration URL and the service URL. Each
service mounts only its own files; the application reads
`APP_DATABASE_URL_FILE` and migrations read `DATABASE_URL_FILE` through one
validated reader. `AGENTIC_SECRETS_DIR` moves the folder where Docker Desktop
cannot read the repository; the scenario uses a private temporary folder per
run and deletes it.

`app` and `migrate` run as UID 10001 and `db` as `postgres` from the start,
all with read-only root file systems, tmpfs for scratch paths, all Linux
capabilities dropped and `no-new-privileges`. The database image removes
`gosu`, whose outdated Go runtime held every fixable HIGH/CRITICAL finding of
the upstream image. The scenario inspects both containers, confirms the API's
database user and has the service role attempt three forbidden changes.

The `container-lab` CI job scans both images with digest-pinned
`aquasec/trivy:0.75.0`, prints all HIGH/CRITICAL findings and fails on those
with a released fix.

## Consequences

A compromised API can still read every document version and credential hash
of its database, and company boundaries remain enforced by the application,
not by row-level security. Secrets are host files without rotation or an
external manager. The scan gate can fail without code changes when upstream
fixes appear, which is the intended signal to update base-image digests;
unfixed findings remain. TLS, corporate identity, scheduled backups, alerting
and a live model are still outside the laboratory.
