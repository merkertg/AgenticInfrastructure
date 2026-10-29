# CI and publication

[← README](../../README.md) · [All documentation](../../README.md#documentation)

The [workflow](../../.github/workflows/ci.yml) runs on pushes and pull requests to
`main`, plus manual dispatch. Python 3.12 and uv install the committed lock.
Separate steps run Ruff, formatting, strict mypy, unit tests, PostgreSQL tests,
documentation links, lock consistency and whitespace checks. PostgreSQL tests
use a disposable PostgreSQL 17 service and a database ending in `_test`.

A second job, `container-lab`, runs `python -m scripts.compose_lab` on a clean
runner: it builds the image, starts a disposable Compose project, runs the
evaluations, the audit-failure and permission-leak drills and a backup and
restore, then prints the JSON report (no tokens or passwords). The checks job
uses the same digest-pinned PostgreSQL image as `compose.yaml`.

The workflow has read-only repository permissions. It does not publish an
image, deploy a service or use corporate credentials. PDF generation is a
local documentation operation described in [the guide index](../guide/README.md).

Before an authorized publication, run [local checks](testing.md), review the
staged diff for secrets and unrelated files, and commit only verified work.
Fetch `origin`, ensure `origin/main` is an ancestor of the local commit, and push
without force. Confirm the remote SHA independently with `git ls-remote`.

A successful push proves publication only. Record GitHub Actions as
`not_verified` until the run for that exact commit is observed to complete.
See [the Russian CI/CD guide](../CI_CD.md) and [verification](../VERIFICATION.md).
