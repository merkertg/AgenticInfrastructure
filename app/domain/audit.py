"""Construct metadata-only audit values without storing or performing operations."""

from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.models import AccessDecision, AccessRequest, ReasonCode


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Describe an authorization attempt, not proof of an executed read or write."""

    request: AccessRequest
    allowed: bool
    reason: ReasonCode
    occurred_at: datetime

    def __post_init__(self) -> None:
        """Check decision consistency and normalize an explicit aware time to UTC."""
        AccessDecision(request=self.request, allowed=self.allowed, reason=self.reason)
        if not isinstance(self.occurred_at, datetime):
            raise TypeError("occurred_at must be a datetime")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        object.__setattr__(self, "occurred_at", self.occurred_at.astimezone(UTC))


def make_audit_event(decision: AccessDecision, *, occurred_at: datetime) -> AuditEvent:
    """Copy a decision and caller-supplied time without consulting clocks or storage."""
    if not isinstance(decision, AccessDecision):
        raise TypeError("decision must be an AccessDecision")
    return AuditEvent(
        request=decision.request,
        allowed=decision.allowed,
        reason=decision.reason,
        occurred_at=occurred_at,
    )
