"""Verify metadata-only audit models without claiming durable event storage."""

from dataclasses import FrozenInstanceError, asdict
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.domain.audit import AuditEvent, make_audit_event
from app.domain.models import AccessDecision, ReasonCode
from tests.domain.fixtures import AccessCase, evaluate_case


def test_allowed_attempt_keeps_exact_evaluated_request(case: AccessCase) -> None:
    """An allowed event records the evaluated actor and target metadata together."""
    decision = evaluate_case(case)
    occurred_at = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    event = make_audit_event(decision, occurred_at=occurred_at)
    assert event.request == case.request
    assert event.allowed is True
    assert event.reason is ReasonCode.ALLOWED
    assert event.occurred_at == occurred_at


def test_denied_attempt_keeps_reason_without_document_body(case: AccessCase) -> None:
    """A denied event explains its outcome without embedding a source document."""
    decision = AccessDecision(
        request=case.request,
        allowed=False,
        reason=ReasonCode.EMPLOYEE_PERMISSION_MISSING,
    )
    event = make_audit_event(
        decision, occurred_at=datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    )
    assert event.allowed is False
    assert event.reason is ReasonCode.EMPLOYEE_PERMISSION_MISSING
    assert set(asdict(event)) == {"request", "allowed", "reason", "occurred_at"}
    assert case.version is not None
    assert case.version.content not in repr(event)
    assert "content" not in asdict(event)["request"]


def test_time_is_explicit_and_normalized_to_utc(case: AccessCase) -> None:
    """A supplied local offset refers to the same stored UTC instant."""
    local = datetime(2026, 10, 24, 17, 0, tzinfo=timezone(timedelta(hours=5)))
    event = make_audit_event(evaluate_case(case), occurred_at=local)
    assert event.occurred_at == datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    assert event.occurred_at.utcoffset() == timedelta(0)


def test_naive_datetime_is_rejected(case: AccessCase) -> None:
    """An event cannot guess the timezone of a caller's timestamp."""
    with pytest.raises((TypeError, ValueError)):
        make_audit_event(evaluate_case(case), occurred_at=datetime(2026, 10, 24, 12, 0))


def test_same_decision_and_explicit_time_produce_same_event(case: AccessCase) -> None:
    """Audit modeling has no hidden clock, randomness or persistence side effects."""
    decision = evaluate_case(case)
    timestamp = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    assert make_audit_event(decision, occurred_at=timestamp) == make_audit_event(
        decision, occurred_at=timestamp
    )


@pytest.mark.parametrize(
    "allowed, reason",
    [(True, ReasonCode.COMPANY_MISMATCH), (False, ReasonCode.ALLOWED)],
)
def test_event_cannot_contradict_outcome_and_reason(
    case: AccessCase, allowed: bool, reason: ReasonCode
) -> None:
    """Direct event construction cannot record contradictory decision metadata."""
    with pytest.raises((TypeError, ValueError)):
        AuditEvent(
            request=case.request,
            allowed=allowed,
            reason=reason,
            occurred_at=datetime(2026, 10, 24, 12, 0, tzinfo=UTC),
        )


def test_event_is_an_immutable_snapshot(case: AccessCase) -> None:
    """An already constructed audit event cannot be edited in place."""
    event = make_audit_event(
        evaluate_case(case), occurred_at=datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    )
    with pytest.raises(FrozenInstanceError):
        event.allowed = False  # type: ignore[misc]
