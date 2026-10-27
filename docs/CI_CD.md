# CI and delivery

[README](../README.md) · [Local results](VERIFICATION.md)

The [workflow](../.github/workflows/ci.yml) runs on pushes to `main`,
pull requests targeting `main`, and manual dispatch. Stages are published
directly to `main`; the former `develop` branch was removed on 2026-10-27. It installs Python
3.12 and uv, synchronizes `uv.lock`, then checks lint, formatting, strict types,
unit tests, local documentation links and whitespace. The PostgreSQL integration
step uses a disposable PostgreSQL 17 service and an explicit local test URL. It
applies and exercises the migration-backed persistence path, including version
conflicts, request replay and audit rollback, and the authenticated memory HTTP
routes, document intake and context search through an in-process client on
the same database. The two suites are distinct:
skipped database tests in a unit run do not establish PostgreSQL behavior.

The workflow has read-only repository permissions and does not deploy a service,
publish a package, build a container or access corporate credentials. Exact
action revisions are pinned. Dependency versions are pinned in `uv.lock`.
The initial resolution uses a fixed two-week release cutoff of
`2026-10-10T00:00:00Z`; this reduces immediate adoption of new releases and does
not replace vulnerability review or a future dependency update policy.

A configured PostgreSQL service or workflow is not evidence of a successful run.
Check the
actual commit and run conclusion before calling CI verified. Publication and
GitHub Actions evidence are recorded separately in the verification report.
