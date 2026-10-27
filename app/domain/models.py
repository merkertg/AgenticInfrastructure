"""Validate immutable memory-policy inputs without authenticating their actors."""

import re
from dataclasses import dataclass, field
from enum import StrEnum

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


class Action(StrEnum):
    """Keep read and write permissions separate, with no implicit inheritance."""

    READ = "read"
    WRITE = "write"


class ReasonCode(StrEnum):
    """Explain the first deterministic policy outcome without document contents."""

    ALLOWED = "allowed"
    COMPANY_UNAVAILABLE = "company_unavailable"
    COMPANY_MISMATCH = "company_mismatch"
    PROJECT_UNAVAILABLE = "project_unavailable"
    PROJECT_MISMATCH = "project_mismatch"
    EMPLOYEE_UNAVAILABLE = "employee_unavailable"
    AGENT_UNAVAILABLE = "agent_unavailable"
    AGENT_EMPLOYEE_MISMATCH = "agent_employee_mismatch"
    DELEGATION_UNAVAILABLE = "delegation_unavailable"
    DELEGATION_MISMATCH = "delegation_mismatch"
    ACTION_NOT_DELEGATED = "action_not_delegated"
    DOCUMENT_UNAVAILABLE = "document_unavailable"
    DOCUMENT_MISMATCH = "document_mismatch"
    VERSION_UNAVAILABLE = "version_unavailable"
    VERSION_MISMATCH = "version_mismatch"
    EMPLOYEE_PERMISSION_MISSING = "employee_permission_missing"
    # Set by the persistence adapter when the authenticated credential lapsed.
    CREDENTIAL_UNAVAILABLE = "credential_unavailable"


def _validate_id(value: str, name: str) -> None:
    """Reject identifiers with unsupported types, lengths or characters."""
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} must be a valid identifier of 1 to 128 characters")


def _validate_label(value: str, name: str) -> None:
    """Keep single-line metadata bounded and free of hidden surrounding space."""
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if (
        not value
        or len(value) > 256
        or value != value.strip()
        or not value.isprintable()
    ):
        raise ValueError(f"{name} must be printable, trimmed and 1 to 256 characters")


def _validate_active(value: bool) -> None:
    """Require an explicit boolean instead of truthy strings or numeric flags."""
    if type(value) is not bool:
        raise TypeError("active must be a boolean")


def _validate_actions(value: frozenset[Action]) -> None:
    """Require an immutable set of actual action values without coercion."""
    if not isinstance(value, frozenset):
        raise TypeError("actions must be a frozenset")
    if any(not isinstance(action, Action) for action in value):
        raise TypeError("actions must contain only Action members")


@dataclass(frozen=True, slots=True)
class Company:
    """Represent a company boundary in the offline policy snapshot."""

    id: str
    name: str
    active: bool = True

    def __post_init__(self) -> None:
        """Reject malformed company metadata before it enters a policy check."""
        _validate_id(self.id, "id")
        _validate_label(self.name, "name")
        _validate_active(self.active)


@dataclass(frozen=True, slots=True)
class Project:
    """Scope documents and delegations to one company project."""

    id: str
    company_id: str
    name: str
    active: bool = True

    def __post_init__(self) -> None:
        """Validate the project identity and its company reference."""
        _validate_id(self.id, "id")
        _validate_id(self.company_id, "company_id")
        _validate_label(self.name, "name")
        _validate_active(self.active)


@dataclass(frozen=True, slots=True)
class Employee:
    """Describe an employee as offline metadata, without proving identity."""

    id: str
    company_id: str
    name: str
    active: bool = True

    def __post_init__(self) -> None:
        """Validate employee metadata independently of authentication."""
        _validate_id(self.id, "id")
        _validate_id(self.company_id, "company_id")
        _validate_label(self.name, "name")
        _validate_active(self.active)


@dataclass(frozen=True, slots=True)
class Agent:
    """Bind an agent registration to an employee within one company."""

    id: str
    company_id: str
    employee_id: str
    name: str
    active: bool = True

    def __post_init__(self) -> None:
        """Validate an agent's declared company, employee and local status."""
        _validate_id(self.id, "id")
        _validate_id(self.company_id, "company_id")
        _validate_id(self.employee_id, "employee_id")
        _validate_label(self.name, "name")
        _validate_active(self.active)


@dataclass(frozen=True, slots=True)
class Delegation:
    """Limit an employee's agent to explicit actions in one company project."""

    id: str
    company_id: str
    project_id: str
    employee_id: str
    agent_id: str
    actions: frozenset[Action]
    active: bool = True

    def __post_init__(self) -> None:
        """Validate delegation references without granting implicit permissions."""
        _validate_id(self.id, "id")
        _validate_id(self.company_id, "company_id")
        _validate_id(self.project_id, "project_id")
        _validate_id(self.employee_id, "employee_id")
        _validate_id(self.agent_id, "agent_id")
        _validate_actions(self.actions)
        _validate_active(self.active)


@dataclass(frozen=True, slots=True)
class Document:
    """Locate a document in one company and project without embedding its text."""

    id: str
    company_id: str
    project_id: str
    title: str
    active: bool = True

    def __post_init__(self) -> None:
        """Validate document identity, scope and bounded display metadata."""
        _validate_id(self.id, "id")
        _validate_id(self.company_id, "company_id")
        _validate_id(self.project_id, "project_id")
        _validate_label(self.title, "title")
        _validate_active(self.active)


@dataclass(frozen=True, slots=True)
class DocumentVersion:
    """Keep one bounded source revision, hiding its content from object repr."""

    id: str
    company_id: str
    document_id: str
    number: int
    source: str
    content: str = field(repr=False)
    active: bool = True

    def __post_init__(self) -> None:
        """Validate revision metadata and bounded text without interpreting it."""
        _validate_id(self.id, "id")
        _validate_id(self.company_id, "company_id")
        _validate_id(self.document_id, "document_id")
        if type(self.number) is not int:
            raise TypeError("number must be an integer")
        if not 1 <= self.number <= 1_000_000:
            raise ValueError("number must be between 1 and 1000000")
        _validate_label(self.source, "source")
        if not isinstance(self.content, str):
            raise TypeError("content must be a string")
        if not 1 <= len(self.content) <= 20_000:
            raise ValueError("content must contain between 1 and 20000 characters")
        _validate_active(self.active)


@dataclass(frozen=True, slots=True)
class Grant:
    """Give one employee explicit document actions inside a company project."""

    id: str
    company_id: str
    project_id: str
    employee_id: str
    document_id: str
    actions: frozenset[Action]
    active: bool = True

    def __post_init__(self) -> None:
        """Validate a grant while allowing an empty action set to authorize nothing."""
        _validate_id(self.id, "id")
        _validate_id(self.company_id, "company_id")
        _validate_id(self.project_id, "project_id")
        _validate_id(self.employee_id, "employee_id")
        _validate_id(self.document_id, "document_id")
        _validate_actions(self.actions)
        _validate_active(self.active)


@dataclass(frozen=True, slots=True)
class AccessRequest:
    """Describe an offline access attempt without authenticating supplied IDs."""

    request_id: str
    company_id: str
    project_id: str
    employee_id: str
    agent_id: str
    document_id: str
    version_id: str
    action: Action

    def __post_init__(self) -> None:
        """Require valid target identifiers and an explicit supported action."""
        _validate_id(self.request_id, "request_id")
        _validate_id(self.company_id, "company_id")
        _validate_id(self.project_id, "project_id")
        _validate_id(self.employee_id, "employee_id")
        _validate_id(self.agent_id, "agent_id")
        _validate_id(self.document_id, "document_id")
        _validate_id(self.version_id, "version_id")
        if not isinstance(self.action, Action):
            raise TypeError("action must be an Action member")


@dataclass(frozen=True, slots=True)
class ScopeRequest:
    """Describe a project-wide attempt, such as a search, without a target document."""

    request_id: str
    company_id: str
    project_id: str
    employee_id: str
    agent_id: str
    action: Action

    def __post_init__(self) -> None:
        """Require valid scope identifiers and an explicit supported action."""
        _validate_id(self.request_id, "request_id")
        _validate_id(self.company_id, "company_id")
        _validate_id(self.project_id, "project_id")
        _validate_id(self.employee_id, "employee_id")
        _validate_id(self.agent_id, "agent_id")
        if not isinstance(self.action, Action):
            raise TypeError("action must be an Action member")


@dataclass(frozen=True, slots=True)
class AccessDecision:
    """Bind a validated decision and its reason to the exact evaluated request."""

    request: AccessRequest
    allowed: bool
    reason: ReasonCode

    def __post_init__(self) -> None:
        """Reject contradictory outcomes and unvalidated decision metadata."""
        if not isinstance(self.request, AccessRequest):
            raise TypeError("request must be an AccessRequest")
        if type(self.allowed) is not bool:
            raise TypeError("allowed must be a boolean")
        if not isinstance(self.reason, ReasonCode):
            raise TypeError("reason must be a ReasonCode member")
        if self.allowed != (self.reason is ReasonCode.ALLOWED):
            raise ValueError("allowed must agree with the reason code")
