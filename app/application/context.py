"""Describe committed context-search results separately from storage details."""

from dataclasses import dataclass, field
from enum import StrEnum

from app.domain.models import ReasonCode, ScopeRequest


class ContextOutcome(StrEnum):
    """Distinguish returned evidence, an explicit empty result and a scope denial."""

    FOUND = "found"
    NO_AUTHORIZED_EVIDENCE = "no_authorized_evidence"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class ContextItem:
    """Return one authorized passage with references that let a caller verify it."""

    document_id: str
    version_id: str
    version_number: int
    source: str
    fragment: int
    start: int
    end: int
    score: float
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ContextResult:
    """Report the committed outcome; denied and empty results carry no items."""

    request: ScopeRequest
    outcome: ContextOutcome
    reason: ReasonCode
    items: tuple[ContextItem, ...] = ()
    truncated: bool = False
