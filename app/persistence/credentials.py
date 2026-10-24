"""Issue and verify hashed agent bearer credentials stored in PostgreSQL."""

import hashlib
import re
import secrets
from datetime import datetime

from sqlalchemy import Connection, Engine, insert, select

from app.application.identity import Principal
from app.domain.models import AccessRequest
from app.persistence import schema as db

TOKEN_PREFIX = "aik1_"
_TOKEN = re.compile(r"aik1_[A-Za-z0-9_-]{43}")


def generate_token() -> str:
    """Create a random bearer secret whose plain value is never stored."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: object) -> str | None:
    """Return the stored digest of a well-formed token, or None for any other input."""
    if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
        return None
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _check_time(value: datetime, name: str) -> None:
    """Require explicit timezone-aware times for issuance and verification."""
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def issue_credential(
    engine: Engine,
    *,
    company_id: str,
    credential_id: str,
    agent_id: str,
    issued_at: datetime,
    expires_at: datetime,
) -> str:
    """Bind a new credential to a registered agent and return its token only once."""
    _check_time(issued_at, "issued_at")
    _check_time(expires_at, "expires_at")
    if expires_at <= issued_at:
        raise ValueError("expires_at must be later than issued_at")
    token = generate_token()
    digest = hash_token(token)
    assert digest is not None
    with engine.begin() as connection:
        employee_id = connection.scalar(
            select(db.agents.c.employee_id).where(
                db.agents.c.company_id == company_id, db.agents.c.id == agent_id
            )
        )
        if employee_id is None:
            raise ValueError("agent registration not found")
        connection.execute(
            insert(db.agent_credentials).values(
                company_id=company_id,
                id=credential_id,
                agent_id=agent_id,
                employee_id=employee_id,
                token_hash=digest,
                issued_at=issued_at,
                expires_at=expires_at,
            )
        )
    return token


def credential_is_current(
    connection: Connection,
    request: AccessRequest,
    credential_id: str,
    at: datetime,
) -> bool:
    """Recheck inside a locked transaction that a credential still matches the actor."""
    row = (
        connection.execute(
            select(db.agent_credentials).where(
                db.agent_credentials.c.company_id == request.company_id,
                db.agent_credentials.c.id == credential_id,
            )
        )
        .mappings()
        .one_or_none()
    )
    return bool(
        row is not None
        and row["active"]
        and row["expires_at"] > at
        and row["agent_id"] == request.agent_id
        and row["employee_id"] == request.employee_id
    )


class CredentialAuthenticator:
    """Resolve bearer tokens to principals without revealing why a token failed."""

    def __init__(self, engine: Engine) -> None:
        """Use an explicit PostgreSQL engine without connecting until first use."""
        self.engine = engine

    def authenticate(self, token: object, *, at: datetime) -> Principal | None:
        """Return the stored actor for an active unexpired token, otherwise None."""
        _check_time(at, "at")
        digest = hash_token(token)
        if digest is None:
            return None
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(db.agent_credentials).where(
                        db.agent_credentials.c.token_hash == digest
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None or not row["active"] or row["expires_at"] <= at:
            return None
        return Principal(
            company_id=row["company_id"],
            employee_id=row["employee_id"],
            agent_id=row["agent_id"],
            credential_id=row["id"],
        )
