"""Import fictional UTF-8 text files into an explicit local lab database."""

import argparse
import json
import os
from pathlib import Path

from app.domain.models import Action
from app.domain.text import MAX_BYTES
from app.persistence.database import make_engine
from app.persistence.intake import (
    IntakeGrant,
    IntakeItem,
    IntakeRejected,
    ingest_documents,
)
from app.persistence.lab import validate_lab_url


def parse_grant(value: str) -> IntakeGrant:
    """Read EMPLOYEE:ACTION[,ACTION] into an explicit grant for the batch."""
    employee_id, separator, actions = value.partition(":")
    if not separator or not employee_id or not actions:
        raise argparse.ArgumentTypeError("use EMPLOYEE:read or EMPLOYEE:read,write")
    try:
        parsed = frozenset(Action(action) for action in actions.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("actions must be read and/or write") from None
    return IntakeGrant(employee_id=employee_id, actions=parsed)


def read_item(path: Path) -> IntakeItem:
    """Read at most one byte past the limit so oversized files are rejected cheaply."""
    with path.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    return IntakeItem(
        document_id=path.stem, title=path.stem, source=f"file:{path.name}", raw=raw
    )


def main(argv: list[str] | None = None) -> int:
    """Import every file or none; print created version IDs or all rejections."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path, help="UTF-8 .txt files")
    parser.add_argument("--project", required=True, help="existing lab project ID")
    parser.add_argument("--company", default="alpha", help="lab company ID")
    parser.add_argument(
        "--grant",
        action="append",
        default=[],
        type=parse_grant,
        help="EMPLOYEE:read[,write]; repeat for several employees",
    )
    args = parser.parse_args(argv)
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        parser.error("set DATABASE_URL for a local PostgreSQL database ending in _lab")
    try:
        validate_lab_url(database_url)
    except ValueError as error:
        parser.error(str(error))
    missing = [str(path) for path in args.files if not path.is_file()]
    if missing:
        parser.error(f"not a file: {', '.join(missing)}")
    items = tuple(read_item(path) for path in args.files)
    engine = make_engine(database_url)
    try:
        versions = ingest_documents(
            engine,
            company_id=args.company,
            project_id=args.project,
            items=items,
            grants=tuple(args.grant),
        )
    except IntakeRejected as rejection:
        print(json.dumps({"imported": [], "rejected": rejection.problems}, indent=2))
        return 1
    finally:
        engine.dispose()
    print(
        json.dumps(
            {
                "imported": [
                    {"document_id": version.document_id, "version_id": version.id}
                    for version in versions
                ],
                "rejected": [],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
