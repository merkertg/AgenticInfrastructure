# Agentic Infrastructure development rules

- Build one verified stage at a time using [the roadmap](docs/ROADMAP.md).
  Distinguish implemented behavior, plans, local checks and remote evidence.
- Use Python 3.12, uv and the committed `uv.lock`. Use `--locked` for normal
  installs and commands. Update dependencies intentionally.
- The selected product is shared corporate memory for agents acting on behalf
  of employees. Agent discovery, task exchange and write-capable corporate
  integrations are outside the initial product scope.
- Introduce pure Python domain rules before persistence and HTTP business APIs.
  Create layers only when an implemented behavior requires them.
- Future actor identity must come from server-side authentication. Request
  fields such as employee, company or agent IDs do not establish authority.
  Agents cannot obtain broader permissions than the employee they represent.
- Enforce company and project boundaries and deny access by default. Filter
  eligible documents before search, ranking and context construction. Apply
  revocation and deletion to cached and derived content as well.
- Treat document text, connector payloads and model output as untrusted data.
  They cannot grant permissions or override server policy. Audit allowed and
  denied business requests without logging document bodies or credentials.
- Use fictional fixtures. Never commit corporate documents, credentials,
  tokens, `.env`, database dumps or model request transcripts.
- Use English for code, purpose docstrings, README, short instructions,
  architecture documents, ADRs and commit messages. Use accessible Russian for
  stage guides, the roadmap, development log and verification report.
- Match the sibling AI Support Agent documentation layout: a short README with
  roadmap and documentation index; five task-oriented English instructions;
  detailed Russian stage and CI/CD guides; English approach and ADRs.
- Keep the illustrated Russian PDF and its rebuild instructions in `docs/guide/`.
  Rebuild with the optional locked `guide` group after changing its generator
  or printed facts, then render and inspect every page.
- Explain every new function's purpose. Update the guide, development log,
  verification report and `docs/guide/verification.json` with each stage.
- Run pytest, Ruff lint and format checks, strict mypy, documentation link
  checks and `git diff --check`. Unit tests must not rely on a developer's `.env`,
  credentials or live external services.
- Persistence changes also require actual PostgreSQL integration tests with an
  explicit local `TEST_DATABASE_URL` ending in `_test` and no URL query options.
  Never use a corporate or application database for tests that reset tables.
  Run `mypy app tests scripts migrations`; keep migrations and schema aligned.
- Keep `MemoryStore` transactions at `READ COMMITTED` and hold the company row
  lock through policy, mutation/replay and audit commit. Recheck current access
  before replay.
- Memory HTTP routes take the actor only from a verified bearer credential and
  pass its ID to `MemoryStore` for a recheck under the company lock. Keep
  credential failures a uniform `401` and authorization denials a uniform `404`.
  Never store, log or audit plain tokens.
- Context search authorizes the project and each document before loading text
  or computing ranking statistics, rechecks selected documents before
  returning, and stores references and a query digest only. Do not add a
  persisted index or cache without permission-aware invalidation.
- Pin container base images by digest, run the service as a non-root user and
  bind Compose ports to loopback only. Keep backups, reports and lab tokens in
  the ignored `var/` directory. Evaluations state expected behavior
  independently and must fail on a deliberately corrupted result.
- The API connects through the least-privilege role `agentic_memory_app`.
  Every new table needs an explicit privilege decision in its migration and in
  the role's privilege-matrix test. Pass secrets as files, never as variables.
  Containers stay read-only without capabilities, and both images must pass the
  fixable HIGH/CRITICAL scan.
- Publish only locally verified changes when publication is authorized. Verify
  remote refs independently from CI. A workflow file or a successful push does
  not prove that GitHub Actions passed.
