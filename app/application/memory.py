"""Describe committed memory outcomes separately from authorization decisions."""

from dataclasses import dataclass
from enum import StrEnum

from app.domain.models import AccessDecision, DocumentVersion


class MemoryOutcome(StrEnum):
    """Distinguish completed operations, denied access and mutation conflicts."""

    READ = "read"
    WRITTEN = "written"
    DENIED = "denied"
    VERSION_CONFLICT = "version_conflict"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"


@dataclass(frozen=True, slots=True)
class MemoryResult:
    """Return read content only after its authorization and audit commit."""

    decision: AccessDecision
    outcome: MemoryOutcome
    version: DocumentVersion | None = None
    version_id: str | None = None
    version_number: int | None = None
    replayed: bool = False
