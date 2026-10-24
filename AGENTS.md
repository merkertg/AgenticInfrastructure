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
  before replay. Do not expose memory over HTTP before the identity stage.
- Publish only locally verified changes when publication is authorized. Verify
  remote refs independently from CI. A workflow file or a successful push does
  not prove that GitHub Actions passed.
