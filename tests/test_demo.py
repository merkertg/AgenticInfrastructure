"""Verify the learning scenario and its independence from web configuration."""

import json
import os
import subprocess
import sys
from pathlib import Path

from app.demo import demo_results
from app.domain.models import Action, ReasonCode

EXPECTED = (
    ("finance_read", True, ReasonCode.ALLOWED, Action.READ),
    ("engineering_finance_denied", False, ReasonCode.PROJECT_MISMATCH, Action.READ),
    ("foreign_company_denied", False, ReasonCode.COMPANY_MISMATCH, Action.READ),
    (
        "missing_grant_denied",
        False,
        ReasonCode.EMPLOYEE_PERMISSION_MISSING,
        Action.READ,
    ),
    (
        "revoked_delegation_denied",
        False,
        ReasonCode.DELEGATION_UNAVAILABLE,
        Action.READ,
    ),
    ("write_denied", False, ReasonCode.EMPLOYEE_PERMISSION_MISSING, Action.WRITE),
    ("version_mismatch_denied", False, ReasonCode.VERSION_MISMATCH, Action.READ),
    ("action_not_delegated", False, ReasonCode.ACTION_NOT_DELEGATED, Action.READ),
)


def test_demo_scenarios_have_independent_expected_outcomes() -> None:
    """The fictional scenario demonstrates both permitted reads and key refusals."""
    outcomes = tuple(
        (name, decision.allowed, decision.reason, decision.request.action)
        for name, decision in demo_results()
    )
    assert outcomes == EXPECTED
    assert demo_results() == demo_results()


def test_demo_cli_ignores_invalid_web_settings() -> None:
    """The offline entry point must not import the ASGI app or parse APP settings."""
    environment = dict(os.environ)
    environment.update(APP_ENV="invalid-web-env", APP_DEBUG="invalid-boolean")
    result = subprocess.run(
        [sys.executable, "-m", "app.demo"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    output = json.loads(result.stdout)
    assert result.stderr == ""
    assert len(output) == len(EXPECTED)
    for entry, (name, allowed, reason, action) in zip(output, EXPECTED, strict=True):
        assert entry == {
            "case": name,
            "request_id": f"demo-{name}",
            "allowed": allowed,
            "reason": reason.value,
            "action": action.value,
            "occurred_at": "2026-10-24T00:00:00+00:00",
        }
