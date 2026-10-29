"""Register, synchronize and inspect the read-only Handbook source in a lab database.

The read-only source token is taken from SOURCE_TOKEN and is never printed,
stored or logged. Only a local PostgreSQL database ending in _lab is accepted.
"""

import argparse
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime

from app.application.source_sync import SyncOutcome
from app.connectors.handbook import HandbookClient, check_token
from app.domain.source import SourceSettings, check_principal
from app.persistence.database import make_engine
from app.persistence.lab import validate_lab_url
from app.persistence.sources import register_source, sync_status, synchronize


def parse_account(value: str) -> tuple[str, str]:
    """Read PRINCIPAL=EMPLOYEE into one source account mapping."""
    principal, separator, employee_id = value.partition("=")
    if not separator or not employee_id:
        raise argparse.ArgumentTypeError("use SOURCE_USER=EMPLOYEE")
    try:
        check_principal(principal)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None
    return principal, employee_id


def _parser() -> argparse.ArgumentParser:
    """Describe the register, run and status commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", default="alpha", help="lab company ID")
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register", help="store a source and accounts")
    register.add_argument("source", help="source ID, e.g. handbook")
    register.add_argument("--project", required=True, help="target lab project")
    register.add_argument("--base-url", required=True, help="source base URL")
    register.add_argument("--space", required=True, help="source space to read")
    register.add_argument("--prefix", required=True, help="memory ID prefix, e.g. hb-")
    register.add_argument(
        "--max-age", type=int, default=900, help="seconds a snapshot vouches for access"
    )
    register.add_argument(
        "--account",
        action="append",
        default=[],
        type=parse_account,
        help="SOURCE_USER=EMPLOYEE; repeat for several accounts",
    )
    for name, text in (
        ("run", "apply one complete snapshot"),
        ("status", "show confirmation and last run"),
    ):
        command = commands.add_parser(name, help=text)
        command.add_argument("source", help="registered source ID")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one command and print JSON; a failed sync exits with status 1."""
    parser = _parser()
    args = parser.parse_args(argv)
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        parser.error("set DATABASE_URL for a local PostgreSQL database ending in _lab")
    try:
        validate_lab_url(database_url)
    except ValueError as error:
        parser.error(str(error))
    engine = make_engine(database_url)
    try:
        if args.command == "register":
            try:
                settings = SourceSettings(
                    id=args.source,
                    company_id=args.company,
                    project_id=args.project,
                    base_url=args.base_url,
                    space=args.space,
                    document_prefix=args.prefix,
                    max_age_seconds=args.max_age,
                )
                register_source(engine, settings, dict(args.account))
            except ValueError as error:
                parser.error(str(error))
            print(
                json.dumps({"registered": args.source, "accounts": len(args.account)})
            )
            return 0
        if args.command == "run":
            try:
                token = check_token(os.environ.get("SOURCE_TOKEN", ""))
            except ValueError as error:
                parser.error(str(error))
            try:
                report = synchronize(
                    engine,
                    lambda settings: HandbookClient(settings, token),
                    company_id=args.company,
                    source_id=args.source,
                )
            except LookupError as error:
                parser.error(str(error))
            print(
                json.dumps(
                    {
                        "run_id": report.run_id,
                        "outcome": report.outcome.value,
                        "failure": report.failure.value if report.failure else None,
                        "counts": asdict(report.counts),
                        "withheld": [
                            {"external_id": external_id, "reason": reason}
                            for external_id, reason in report.withheld
                        ],
                    },
                    indent=2,
                )
            )
            return 0 if report.outcome is SyncOutcome.SUCCEEDED else 1
        try:
            status = sync_status(
                engine,
                company_id=args.company,
                source_id=args.source,
                now=datetime.now(UTC),
            )
        except LookupError as error:
            parser.error(str(error))
        print(json.dumps(status, indent=2))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
