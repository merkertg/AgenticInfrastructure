"""Issue a fictional agent credential in an explicit local lab database."""

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.persistence.credentials import issue_credential
from app.persistence.database import make_engine
from app.persistence.lab import validate_lab_url


def main(argv: list[str] | None = None) -> int:
    """Print a new token once; the lab database stores only its digest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("agent_id", help="registered lab agent, e.g. fin-agent")
    parser.add_argument("--company", default="alpha", help="lab company ID")
    parser.add_argument(
        "--hours", type=int, default=8, help="lifetime from 1 to 168 hours"
    )
    args = parser.parse_args(argv)
    if not 1 <= args.hours <= 168:
        parser.error("--hours must be between 1 and 168")
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        parser.error("set DATABASE_URL for a local PostgreSQL database ending in _lab")
    try:
        validate_lab_url(database_url)
    except ValueError as error:
        parser.error(str(error))
    issued_at = datetime.now(UTC)
    expires_at = issued_at + timedelta(hours=args.hours)
    credential_id = f"cred-{uuid4()}"
    engine = make_engine(database_url)
    try:
        token = issue_credential(
            engine,
            company_id=args.company,
            credential_id=credential_id,
            agent_id=args.agent_id,
            issued_at=issued_at,
            expires_at=expires_at,
        )
    except ValueError as error:
        parser.error(str(error))
    finally:
        engine.dispose()
    print(
        json.dumps(
            {
                "credential_id": credential_id,
                "company_id": args.company,
                "agent_id": args.agent_id,
                "expires_at": expires_at.isoformat(),
                "token": token,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
