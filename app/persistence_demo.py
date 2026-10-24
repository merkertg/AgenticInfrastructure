"""Exercise persistent memory against an explicit fictional local lab database."""

import argparse
import json
import os
from dataclasses import replace
from datetime import UTC, datetime

from app.application.memory import MemoryResult
from app.domain.models import AccessRequest, Action, DocumentVersion
from app.persistence.database import make_engine, migrate
from app.persistence.lab import seed_lab, validate_lab_url
from app.persistence.memory import MemoryStore


def run_demo(store: MemoryStore) -> tuple[tuple[str, MemoryResult], ...]:
    """Read, append, retry and conflict on fictional data without printing content."""
    timestamp = datetime(2026, 10, 24, tzinfo=UTC)
    request = AccessRequest(
        request_id="lab-read-v1",
        company_id="alpha",
        project_id="finance",
        employee_id="fin-user",
        agent_id="fin-agent",
        document_id="policy",
        version_id="policy-v1",
        action=Action.READ,
    )
    write = replace(request, request_id="lab-write-v2", action=Action.WRITE)
    version = DocumentVersion(
        id="policy-v2",
        company_id="alpha",
        document_id="policy",
        number=2,
        source="fixture:policy",
        content="Fictional revision two.",
    )
    return (
        ("read_v1", store.read_version(request, occurred_at=timestamp)),
        (
            "append_v2",
            store.write_version(
                write, new_version=version, expected_number=1, occurred_at=timestamp
            ),
        ),
        (
            "retry_v2",
            store.write_version(
                write, new_version=version, expected_number=1, occurred_at=timestamp
            ),
        ),
        (
            "read_v2",
            store.read_version(
                replace(request, request_id="lab-read-v2", version_id="policy-v2"),
                occurred_at=timestamp,
            ),
        ),
        (
            "denied_engineer",
            store.read_version(
                replace(
                    request,
                    request_id="lab-engineer",
                    project_id="engineering",
                    employee_id="eng-user",
                    agent_id="eng-agent",
                ),
                occurred_at=timestamp,
            ),
        ),
        (
            "stale_write",
            store.write_version(
                replace(write, request_id="lab-stale-write"),
                new_version=replace(version, id="stale-v2"),
                expected_number=1,
                occurred_at=timestamp,
            ),
        ),
        (
            "changed_retry",
            store.write_version(
                write,
                new_version=replace(version, content="Different fictional revision."),
                expected_number=1,
                occurred_at=timestamp,
            ),
        ),
    )


def main() -> int:
    """Set up or exercise only an explicitly configured local lab database."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--setup", action="store_true", help="migrate and seed an empty lab once"
    )
    args = parser.parse_args()
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        parser.error("set DATABASE_URL for a local PostgreSQL database ending in _lab")
    validate_lab_url(database_url)
    if args.setup:
        migrate(database_url)
    engine = make_engine(database_url)
    try:
        if args.setup:
            seed_lab(engine)
            print(json.dumps({"setup": "created", "data": "fictional"}))
        else:
            results = run_demo(MemoryStore(engine))
            print(
                json.dumps(
                    [
                        {
                            "case": name,
                            "outcome": result.outcome.value,
                            "allowed": result.decision.allowed,
                            "reason": result.decision.reason.value,
                            "version_id": result.version_id,
                            "number": result.version_number,
                            "replayed": result.replayed,
                        }
                        for name, result in results
                    ],
                    indent=2,
                )
            )
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
