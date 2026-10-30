"""Reproduce the full Stage 08 laboratory from a clean, disposable Compose project.

The scenario builds the image, starts its own Compose project on loopback ports,
seeds fictional data through trusted lab setup, synchronizes the fictional
source and runs the independent evaluations. It checks that the API runs
locked down as the least-privilege role, injects an audit failure and a
permission leak that the checks must catch, backs the database up,
destroys the volume, restores the backup and evaluates again. Secret files
live in a private temporary folder for the run and tokens only in this
process; backups and the report go to var/, which Git ignores.
"""

import argparse
import json
import os
import secrets
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError

from app.application.source_sync import SyncOutcome
from app.connectors.handbook import HandbookClient
from app.domain.models import Action
from app.domain.source import SourceSettings
from app.persistence import schema as db
from app.persistence.credentials import issue_credential
from app.persistence.database import make_engine
from app.persistence.intake import (
    IntakeGrant,
    IntakeItem,
    IntakeRejected,
    ingest_documents,
)
from app.persistence.lab import seed_lab, seed_second_company
from app.persistence.sources import register_source, synchronize
from app.source_lab import LabServer, load_snapshot
from scripts.evaluate import (
    Api,
    CaseResult,
    load_cases,
    resolve,
    run_cases,
    summary,
)
from scripts.lab_secrets import APP_LOGIN, write_secrets

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
VAR = ROOT / "var"
PROJECT = "agentic-stage08"
READ = frozenset({Action.READ})
CORE_TABLES = (
    db.companies,
    db.projects,
    db.employees,
    db.agents,
    db.agent_credentials,
    db.delegations,
    db.documents,
    db.versions,
    db.grants,
    db.sources,
    db.source_accounts,
    db.source_documents,
)
# The permission leak must break at least these independent expectations.
LEAK_CASES = frozenset({"cross-project-context", "cross-project-read"})


class Lab:
    """Drive one disposable Compose project with secret files made for this run."""

    def __init__(self, project: str, app_port: int, db_port: int) -> None:
        """Write fresh secret files to a private temporary folder outside the repo.

        Docker Desktop may be unable to read a repository under a protected
        macOS folder, and per-run secrets should never sit next to the code.
        """
        directory = Path(tempfile.mkdtemp(prefix=f"{project}-secrets-"))
        self.secrets = write_secrets(directory)
        self.project = project
        self.env = {
            **os.environ,
            "AGENTIC_SECRETS_DIR": str(directory),
            "APP_PORT": str(app_port),
            "POSTGRES_PORT": str(db_port),
        }
        self.database_url = self.secrets.host_url(db_port)
        self.app_database_url = self.secrets.host_url(db_port, app=True)
        self.base_url = f"http://127.0.0.1:{app_port}"

    def remove_secrets(self) -> None:
        """Delete this run's secret folder."""
        shutil.rmtree(self.secrets.directory, ignore_errors=True)

    def __repr__(self) -> str:
        """Show the project and API address without the password."""
        return f"Lab(project={self.project!r}, base_url={self.base_url!r})"

    def compose(self, *args: str, stdin: bytes | None = None) -> bytes:
        """Run docker compose for this project and return its standard output."""
        completed = subprocess.run(
            ["docker", "compose", "--project-name", self.project, *args],
            cwd=ROOT,
            env=self.env,
            input=stdin,
            stdout=subprocess.PIPE,
            check=True,
        )
        return completed.stdout


def step(name: str) -> None:
    """Print progress to standard error, keeping standard output for the report."""
    print(f"[{datetime.now(UTC):%H:%M:%S}] {name}", file=sys.stderr, flush=True)


def _ingest(
    engine: Engine, company_id: str, project_id: str, folder: str, employee: str
) -> int:
    """Import every fictional text of one fixture folder for one reader."""
    items = tuple(
        IntakeItem(
            document_id=path.stem,
            title=path.stem,
            source=f"file:{path.name}",
            raw=path.read_bytes(),
        )
        for path in sorted((FIXTURES / "lab" / folder).glob("*.txt"))
    )
    ingest_documents(
        engine,
        company_id=company_id,
        project_id=project_id,
        items=items,
        grants=(IntakeGrant(employee_id=employee, actions=READ),),
    )
    return len(items)


@contextmanager
def _source_server(token: str) -> Iterator[LabServer]:
    """Serve the first Handbook fixture on a free loopback port."""
    server = LabServer(load_snapshot(FIXTURES / "source" / "handbook-1.json"), token)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def seed(engine: Engine) -> tuple[dict[str, str], dict[str, Any]]:
    """Create the fictional lab, sync both source states and issue agent tokens."""
    seed_lab(engine)
    seed_second_company(engine)
    imported = {
        "finance": _ingest(engine, "alpha", "finance", "finance", "fin-user"),
        "engineering": _ingest(
            engine, "alpha", "engineering", "engineering", "eng-user"
        ),
        "research": _ingest(engine, "beta", "research", "beta", "beta-user"),
    }
    try:
        ingest_documents(
            engine,
            company_id="alpha",
            project_id="finance",
            items=(
                IntakeItem(
                    document_id="broken-scan",
                    title="Broken scan",
                    source="file:broken-scan.txt",
                    raw=b"\xff\xfe not text",
                ),
            ),
        )
        corrupted: object = "imported"
    except IntakeRejected as rejection:
        corrupted = [list(problem) for problem in rejection.problems]
    source_token = secrets.token_urlsafe(32)
    syncs: list[dict[str, Any]] = []
    with _source_server(source_token) as server:
        settings = SourceSettings(
            id="handbook",
            company_id="alpha",
            project_id="finance",
            base_url=f"http://127.0.0.1:{server.server_address[1]}",
            space="alpha-handbook",
            document_prefix="hb-",
            max_age_seconds=900,
        )
        register_source(engine, settings, {"u-fin": "fin-user", "u-eng": "eng-user"})
        for snapshot in ("handbook-1.json", "handbook-2.json"):
            server.snapshot = load_snapshot(FIXTURES / "source" / snapshot)
            report = synchronize(
                engine,
                lambda value: HandbookClient(value, source_token),
                company_id="alpha",
                source_id="handbook",
            )
            if report.outcome is not SyncOutcome.SUCCEEDED:
                raise RuntimeError(f"source sync failed: {report.failure}")
            syncs.append(
                {
                    "snapshot": snapshot,
                    "created": report.counts.created,
                    "updated": report.counts.updated,
                    "deleted": report.counts.deleted,
                    "withheld": [list(entry) for entry in report.withheld],
                    "grants_revoked": report.counts.grants_revoked,
                }
            )
    now = datetime.now(UTC)
    tokens = {
        name: issue_credential(
            engine,
            company_id=company,
            credential_id=f"cred-{name}-{secrets.token_hex(4)}",
            agent_id=agent,
            issued_at=now,
            expires_at=now + timedelta(hours=2),
        )
        for name, company, agent in (
            ("fin", "alpha", "fin-agent"),
            ("eng", "alpha", "eng-agent"),
            ("beta", "beta", "beta-agent"),
        )
    }
    return tokens, {
        "imported": imported,
        "corrupted_intake": corrupted,
        "source_syncs": syncs,
    }


def counts(engine: Engine) -> dict[str, int]:
    """Count rows of the tables that define memory and access."""
    with engine.connect() as connection:
        return {
            table.name: int(
                connection.scalar(select(func.count()).select_from(table)) or 0
            )
            for table in CORE_TABLES
        }


def audit_drill(engine: Engine, api: Api) -> dict[str, Any]:
    """Make audit inserts fail; the read must fail closed without document text."""
    path = resolve(
        "/memory/projects/finance/documents/travel-policy/versions/"
        "{version:alpha:travel-policy:1}"
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE FUNCTION lab_fail_audit() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RAISE EXCEPTION 'lab audit failure'; END $$"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER lab_fail_audit BEFORE INSERT ON audit_events "
                "FOR EACH ROW EXECUTE FUNCTION lab_fail_audit()"
            )
        )
    try:
        before = counts_of(engine, db.audit_events)
        failed = api.request("fin", "GET", path)
        after = counts_of(engine, db.audit_events)
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP TRIGGER lab_fail_audit ON audit_events"))
            connection.execute(text("DROP FUNCTION lab_fail_audit()"))
    recovered = api.request("fin", "GET", path)
    return {
        "status_during_failure": failed.status,
        "body_during_failure": failed.body,
        "text_withheld": "500 credits" not in failed.text,
        "audit_rows_unchanged": before == after,
        "status_after_recovery": recovered.status,
        "passed": failed.status == 503
        and "500 credits" not in failed.text
        and before == after
        and recovered.status == 200,
    }


def counts_of(engine: Engine, table: Any) -> int:
    """Count the rows of one table."""
    with engine.connect() as connection:
        return int(connection.scalar(select(func.count()).select_from(table)) or 0)


def tamper_drill(
    engine: Engine, api: Api, cases: list[dict[str, Any]]
) -> dict[str, Any]:
    """Leak finance access to the engineering agent; the evaluations must fail."""
    with engine.begin() as connection:
        connection.execute(
            db.delegations.insert().values(
                company_id="alpha",
                id="tamper-scope",
                project_id="finance",
                employee_id="eng-user",
                agent_id="eng-agent",
                actions=["read"],
            )
        )
        connection.execute(
            db.grants.insert().values(
                company_id="alpha",
                id="tamper-grant",
                project_id="finance",
                employee_id="eng-user",
                document_id="travel-policy",
                actions=["read"],
            )
        )
    try:
        leaked = run_cases(api, cases)
    finally:
        with engine.begin() as connection:
            connection.execute(
                db.grants.delete().where(db.grants.c.id == "tamper-grant")
            )
            connection.execute(
                db.delegations.delete().where(db.delegations.c.id == "tamper-scope")
            )
    repaired = run_cases(api, cases)
    detected = sorted(result.case_id for result in leaked if not result.passed)
    return {
        "failed_cases_during_leak": detected,
        "passed_after_repair": all(result.passed for result in repaired),
        "passed": LEAK_CASES <= set(detected)
        and all(result.passed for result in repaired),
    }


def _outcomes(results: list[CaseResult]) -> list[tuple[str, int, bool]]:
    """Reduce results to comparable (case, status, passed) triples."""
    return [(result.case_id, result.status, result.passed) for result in results]


def backup_restore(
    lab: Lab, engine: Engine, api: Api, cases: list[dict[str, Any]]
) -> dict[str, Any]:
    """Dump, destroy the volume, restore into a fresh one and compare."""
    before_counts = counts(engine)
    before = run_cases(api, cases)
    dump = lab.compose(
        "exec",
        "-T",
        "db",
        "pg_dump",
        "--username=agentic",
        "--dbname=agentic_lab",
        "--format=custom",
    )
    backups = VAR / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    path = backups / f"{lab.project}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.dump"
    path.write_bytes(dump)
    path.chmod(0o600)
    engine.dispose()
    started = time.perf_counter()
    lab.compose("down", "--volumes")
    lab.compose("up", "--detach", "--wait", "db")
    lab.compose(
        "exec",
        "-T",
        "db",
        "pg_restore",
        "--username=agentic",
        "--dbname=agentic_lab",
        "--exit-on-error",
        "--no-owner",
        stdin=dump,
    )
    lab.compose("up", "--detach", "--wait")
    restore_seconds = round(time.perf_counter() - started, 1)
    after_counts = counts(engine)
    after = run_cases(api, cases)
    return {
        "backup_file": str(path.relative_to(ROOT)),
        "backup_bytes": len(dump),
        "restore_seconds": restore_seconds,
        "row_counts": after_counts,
        "row_counts_equal": before_counts == after_counts,
        "evaluations_equal": _outcomes(before) == _outcomes(after),
        "evaluations_after_restore": summary(after),
        "passed": before_counts == after_counts
        and _outcomes(before) == _outcomes(after)
        and all(result.passed for result in after),
    }


def measure(api: Api, repeat: int) -> dict[str, Any]:
    """Time sequential requests; the numbers describe this machine only."""

    def timed(call: Callable[[], int]) -> dict[str, Any]:
        """Run one call repeatedly and summarize its latency in milliseconds."""
        samples: list[float] = []
        statuses: set[int] = set()
        for _ in range(repeat):
            started = time.perf_counter()
            statuses.add(call())
            samples.append((time.perf_counter() - started) * 1000)
        ordered = sorted(samples)
        return {
            "requests": repeat,
            "statuses": sorted(statuses),
            "p50_ms": round(statistics.median(ordered), 1),
            "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 1),
            "max_ms": round(ordered[-1], 1),
        }

    return {
        "ready": timed(lambda: api.request(None, "GET", "/ready").status),
        "context": timed(
            lambda: (
                api.request(
                    "fin",
                    "POST",
                    "/memory/projects/finance/context",
                    body={"query": "travel limit per trip", "max_items": 5},
                ).status
            )
        ),
        "answer": timed(
            lambda: (
                api.request(
                    "fin",
                    "POST",
                    "/memory/projects/finance/answers",
                    body={"question": "What is the travel limit per trip?"},
                ).status
            )
        ),
    }


def environment() -> dict[str, Any]:
    """Describe the Docker host the measurements and checks ran on."""

    def docker(*args: str) -> str:
        """Return the trimmed output of one docker command."""
        return subprocess.run(
            ["docker", *args], capture_output=True, text=True, check=True
        ).stdout.strip()

    return {
        "docker_server": docker("version", "--format", "{{.Server.Version}}"),
        "compose": docker("compose", "version", "--short"),
        "docker_host": docker(
            "info", "--format", "{{.OperatingSystem}} {{.Architecture}}"
        ),
        "cpus": int(docker("info", "--format", "{{.NCPU}}")),
        "memory_bytes": int(docker("info", "--format", "{{.MemTotal}}")),
        "image_bytes": int(
            docker("image", "inspect", "--format", "{{.Size}}", _image())
        ),
        "db_image_bytes": int(
            docker(
                "image",
                "inspect",
                "--format",
                "{{.Size}}",
                os.environ.get("DB_IMAGE", "agentic-infrastructure-db:local"),
            )
        ),
    }


def _inspect(lab: Lab, service: str) -> dict[str, Any]:
    """Return docker inspect data of one running service container."""
    container = lab.compose("ps", "--quiet", service).decode().strip()
    data: list[dict[str, Any]] = json.loads(
        subprocess.run(
            ["docker", "inspect", container], capture_output=True, check=True
        ).stdout
    )
    return data[0]


def _locked_down(inspected: dict[str, Any], user: str) -> bool:
    """Tell whether a container runs as the user, read-only, without capabilities."""
    host = inspected["HostConfig"]
    return (
        host["ReadonlyRootfs"] is True
        and host["CapDrop"] == ["ALL"]
        and "no-new-privileges:true" in (host["SecurityOpt"] or [])
        and inspected["Config"]["User"] == user
    )


def hardening_check(lab: Lab, engine: Engine) -> dict[str, Any]:
    """Confirm both containers run locked down and the API uses the service role."""
    inspected, database = _inspect(lab, "app"), _inspect(lab, "db")
    host, config = inspected["HostConfig"], inspected["Config"]
    environment = " ".join(
        [*(config.get("Env") or []), *(database["Config"].get("Env") or [])]
    )
    with engine.connect() as connection:
        users = sorted(
            connection.scalars(
                text(
                    "SELECT DISTINCT usename FROM pg_stat_activity "
                    "WHERE datname = 'agentic_lab' AND backend_type = 'client backend'"
                )
            )
        )
    denied: dict[str, str | None] = {}
    restricted = make_engine(lab.app_database_url)
    try:
        for name, statement in (
            ("widen_grant", 'UPDATE grants SET actions = \'["read", "write"]\''),
            ("extend_source", "UPDATE sources SET confirmed_at = now()"),
            ("erase_audit", "DELETE FROM audit_events"),
        ):
            try:
                with restricted.begin() as connection:
                    connection.execute(text(statement))
                denied[name] = None
            except DBAPIError as error:
                denied[name] = getattr(error.orig, "sqlstate", None)
    finally:
        restricted.dispose()
    secrets_in_env = any(
        value in environment
        for value in (lab.secrets.owner_password, lab.secrets.app_password)
    )
    result = {
        "read_only_root": host["ReadonlyRootfs"],
        "cap_drop": host["CapDrop"],
        "security_opt": host["SecurityOpt"],
        "user": config["User"],
        "password_in_environment": secrets_in_env,
        "database_users": users,
        "service_role_denials": denied,
    }
    result["db_locked_down"] = _locked_down(database, "postgres")
    result["passed"] = (
        _locked_down(inspected, "app")
        and result["db_locked_down"]
        and not secrets_in_env
        and APP_LOGIN in users
        and all(code == "42501" for code in denied.values())
    )
    return result


def _image() -> str:
    """Name the application image Compose builds."""
    return os.environ.get("APP_IMAGE", "agentic-infrastructure:local")


def main(argv: list[str] | None = None) -> int:
    """Run the whole scenario and print a JSON report; exit 1 if any check fails."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=PROJECT, help="disposable project name")
    parser.add_argument("--app-port", type=int, default=18005)
    parser.add_argument("--db-port", type=int, default=55438)
    parser.add_argument("--repeat", type=int, default=30, help="timed requests")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="leave the stack running and save lab tokens to var/lab/tokens.json",
    )
    args = parser.parse_args(argv)
    if (
        not args.project.startswith("agentic-")
        or args.project == "agentic-infrastructure"
    ):
        parser.error("use a disposable project name such as agentic-stage08")
    lab = Lab(args.project, args.app_port, args.db_port)
    cases = load_cases()
    report: dict[str, Any] = {"project": args.project, "started_at": None}
    engine: Engine | None = None
    passed = False
    try:
        report["started_at"] = datetime.now(UTC).isoformat()
        step("removing any previous run of this disposable project")
        lab.compose("down", "--volumes", "--remove-orphans")
        step("building the image and starting db, migrate and app")
        lab.compose("build")
        lab.compose("up", "--detach", "--wait")
        engine = make_engine(lab.database_url)
        step("seeding fictional data, syncing the source, issuing tokens")
        tokens, report["seed"] = seed(engine)
        api = Api(lab.base_url, tokens)
        step("running independent evaluations")
        initial = run_cases(api, cases)
        report["evaluations"] = summary(initial)
        step("container and database role hardening")
        report["hardening"] = hardening_check(lab, engine)
        step("audit failure drill")
        report["audit_drill"] = audit_drill(engine, api)
        step("permission leak drill")
        report["tamper_drill"] = tamper_drill(engine, api, cases)
        step("backup, volume loss and restore")
        report["backup_restore"] = backup_restore(lab, engine, api, cases)
        step("timing sequential requests")
        report["measurements"] = measure(api, args.repeat)
        report["environment"] = environment()
        passed = (
            all(result.passed for result in initial)
            and report["seed"]["corrupted_intake"] == [["broken-scan", "not_utf8"]]
            and report["hardening"]["passed"]
            and report["audit_drill"]["passed"]
            and report["tamper_drill"]["passed"]
            and report["backup_restore"]["passed"]
            and all(
                entry["statuses"] == [200] for entry in report["measurements"].values()
            )
        )
        if args.keep:
            saved = VAR / "lab" / "tokens.json"
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.touch(mode=0o600)
            saved.write_text(json.dumps(tokens), encoding="utf-8")
            report["kept"] = {
                "base_url": lab.base_url,
                "tokens": str(saved.relative_to(ROOT)),
                "secrets": str(lab.secrets.directory),
            }
    except (subprocess.CalledProcessError, RuntimeError, OSError) as error:
        report["error"] = type(error).__name__
        step(f"scenario stopped: {type(error).__name__}")
        subprocess.run(
            [
                "docker",
                "compose",
                "--project-name",
                lab.project,
                "logs",
                "--tail",
                "40",
            ],
            cwd=ROOT,
            env=lab.env,
            check=False,
        )
    finally:
        if engine is not None:
            engine.dispose()
        if not args.keep:
            step("removing the disposable project, its volume and secrets")
            lab.compose("down", "--volumes", "--remove-orphans")
            lab.remove_secrets()
    report["passed"] = passed
    report["finished_at"] = datetime.now(UTC).isoformat()
    reports = VAR / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"{args.project}.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    json.dump(report, sys.stdout, indent=2, default=str)
    print()
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
