"""Verify bounded immutable domain inputs before policies consume them."""

import ast
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any, cast

import pytest

from app.domain.models import (
    AccessDecision,
    AccessRequest,
    Action,
    Company,
    ReasonCode,
)
from tests.domain.fixtures import AccessCase


@pytest.mark.parametrize(
    "identifier", ["", " ", "alpha project", " alpha", "alpha\n", "компания", "a" * 129]
)
def test_unsupported_identifiers_are_rejected(identifier: str) -> None:
    """Ambiguous, non-ASCII, empty or oversized IDs cannot enter a snapshot."""
    with pytest.raises((TypeError, ValueError)):
        Company(id=identifier, name="Alpha")


def test_identifier_bounds_and_supported_separators_are_preserved() -> None:
    """Supported identifiers are retained exactly instead of normalized silently."""
    identifier = "A0_.:-" + "z" * 122
    assert len(identifier) == 128
    assert Company(id=identifier, name="Alpha").id == identifier


@pytest.mark.parametrize("name", ["", " Alpha", "Alpha ", "Alpha\nTeam", "a" * 257])
def test_invalid_display_metadata_is_rejected(name: str) -> None:
    """Display metadata must be bounded, single-line and explicitly trimmed."""
    with pytest.raises((TypeError, ValueError)):
        Company(id="alpha", name=name)


def test_labels_can_use_printable_unicode() -> None:
    """Human-readable metadata may use the employee's language."""
    assert Company(id="alpha", name="Компания Alpha").name == "Компания Alpha"


@pytest.mark.parametrize("value", ["true", 1, None])
def test_truthy_flags_are_not_silently_accepted(value: object) -> None:
    """A string or integer cannot accidentally activate an entity."""
    with pytest.raises((TypeError, ValueError)):
        Company(id="alpha", name="Alpha", active=cast(bool, value))


@pytest.mark.parametrize("actions", [{Action.READ}, [Action.READ], frozenset({"read"})])
def test_permissions_require_an_immutable_set_of_actual_actions(
    case: AccessCase, actions: object
) -> None:
    """Mutable collections and raw action strings cannot become trusted scopes."""
    assert case.delegation is not None
    with pytest.raises((TypeError, ValueError)):
        replace(case.delegation, actions=cast(frozenset[Action], actions))


def test_empty_action_set_is_valid_but_contains_no_permission(case: AccessCase) -> None:
    """An explicitly empty scope remains representable for deny-default decisions."""
    assert case.delegation is not None
    assert replace(case.delegation, actions=frozenset()).actions == frozenset()
    assert replace(case.grants[0], actions=frozenset()).actions == frozenset()


@pytest.mark.parametrize("number", [0, -1, 1_000_001, True, 1.0])
def test_version_numbers_are_bounded_exact_integers(
    case: AccessCase, number: object
) -> None:
    """Revision numbers reject booleans, floats and out-of-range integers."""
    assert case.version is not None
    with pytest.raises((TypeError, ValueError)):
        replace(case.version, number=cast(int, number))


@pytest.mark.parametrize("number", [1, 1_000_000])
def test_documented_version_bounds_are_inclusive(case: AccessCase, number: int) -> None:
    """Both documented revision-number boundaries are accepted."""
    assert case.version is not None
    assert replace(case.version, number=number).number == number


@pytest.mark.parametrize("content", ["", "a" * 20_001, None])
def test_version_content_is_nonempty_bounded_text(
    case: AccessCase, content: object
) -> None:
    """Empty, oversized or non-text input is rejected before use as memory."""
    assert case.version is not None
    with pytest.raises((TypeError, ValueError)):
        replace(case.version, content=cast(str, content))


def test_content_keeps_linebreaks_and_is_hidden_from_repr(case: AccessCase) -> None:
    """Version text is preserved without appearing in ordinary object repr output."""
    assert case.version is not None
    text = "Private fictional text.\nSecond line."
    version = replace(case.version, content=text)
    assert version.content == text
    assert text not in repr(version)
    assert "Private fictional text" not in repr(version)


@pytest.mark.parametrize("field", ["document_id", "version_id", "employee_id"])
def test_request_references_are_validated(case: AccessCase, field: str) -> None:
    """Targets and actor references receive the same identifier constraints."""
    with pytest.raises((TypeError, ValueError)):
        replace(case.request, **cast(dict[str, Any], {field: "invalid reference"}))


def test_request_requires_action_enum_instead_of_raw_string(case: AccessCase) -> None:
    """A caller cannot bypass explicit action validation with an equal string."""
    with pytest.raises((TypeError, ValueError)):
        replace(case.request, action=cast(Action, "read"))


@pytest.mark.parametrize(
    "allowed, reason",
    [(True, ReasonCode.EMPLOYEE_PERMISSION_MISSING), (False, ReasonCode.ALLOWED)],
)
def test_contradictory_decisions_are_rejected(
    case: AccessCase, allowed: bool, reason: ReasonCode
) -> None:
    """A decision cannot claim success while carrying a denial reason or vice versa."""
    with pytest.raises((TypeError, ValueError)):
        AccessDecision(request=case.request, allowed=allowed, reason=reason)


def test_decision_rejects_an_unvalidated_request() -> None:
    """Decision metadata must use a constructed request rather than arbitrary data."""
    with pytest.raises((TypeError, ValueError)):
        AccessDecision(
            request=cast(AccessRequest, {}), allowed=True, reason=ReasonCode.ALLOWED
        )


def test_frozen_entities_and_scopes_cannot_be_changed_in_place(
    case: AccessCase,
) -> None:
    """Policy inputs remain immutable snapshots during repeated evaluations."""
    assert case.agent is not None
    with pytest.raises(FrozenInstanceError):
        case.agent.employee_id = "employee-engineering"  # type: ignore[misc]
    assert case.delegation is not None
    assert isinstance(case.delegation.actions, frozenset)
    assert case.agent.employee_id == "employee-finance"


def test_domain_imports_only_standard_library_or_its_own_modules() -> None:
    """Framework, configuration and infrastructure imports stay outside the domain."""
    domain = Path(__file__).resolve().parents[2] / "app" / "domain"
    sources = sorted(domain.glob("*.py"))
    assert sources
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.level:
                    continue
                modules = [node.module]
            else:
                continue
            for module in modules:
                assert (
                    module.split(".")[0] in sys.stdlib_module_names
                    or module == "app.domain"
                    or module.startswith("app.domain.")
                ), f"{path.name} imports non-domain dependency {module}"
