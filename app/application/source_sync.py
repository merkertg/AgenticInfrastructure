"""Collect one complete, bounded source snapshot before memory is changed."""

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from app.domain.source import (
    MAX_PAGE_BYTES,
    MAX_PAGES,
    MAX_SOURCE_DOCUMENTS,
    ListedDocument,
    ListingRejected,
    parse_listing_page,
)
from app.domain.text import TextRejected, TextRejection, decode_text

REQUEST_SECONDS = 5.0
SYNC_SECONDS = 60.0


class SyncFailure(StrEnum):
    """Name why a sync applied nothing; codes never contain payload or secrets."""

    SOURCE_DISABLED = "source_disabled"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    UNAUTHORIZED = "unauthorized"
    UNEXPECTED_RESPONSE = "unexpected_response"
    INVALID_LISTING = "invalid_listing"
    LISTING_LIMIT = "listing_limit"
    DOCUMENT_CONFLICT = "document_conflict"
    PROJECT_DOCUMENT_LIMIT = "project_document_limit"
    SUPERSEDED = "superseded"


class SyncFailed(Exception):
    """Stop a sync with a failure code; the message is the code only."""

    def __init__(self, failure: SyncFailure) -> None:
        """Keep the machine-readable failure alongside the exception text."""
        super().__init__(failure.value)
        self.failure = failure


class SourceClient(Protocol):
    """Read one configured source; implementations raise SyncFailed on errors.

    Each method returns at most one byte more than the limit the caller checks:
    MAX_PAGE_BYTES for a listing page and the intake MAX_BYTES for a text.
    """

    def list_page(self, cursor: str | None, *, timeout: float) -> bytes:
        """Return the raw listing page that starts at the opaque cursor."""
        ...

    def fetch_content(
        self, external_id: str, revision: str, *, timeout: float
    ) -> bytes:
        """Return the raw text of one listed document revision."""
        ...


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Hold the complete listing and the texts fetched for new revisions."""

    documents: tuple[ListedDocument, ...]
    contents: Mapping[str, str | TextRejection] = field(repr=False)


def collect_snapshot(
    client: SourceClient,
    needs: Callable[[ListedDocument], bool],
    *,
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
) -> Snapshot:
    """Read every listing page, then the texts that `needs` selects.

    Deletion is inferred from absence, so a partial listing is never returned:
    a loop, a duplicate, too many pages or documents, or the deadline fails it.
    """

    def budget() -> float:
        """Check the sync deadline and cap the next request's remaining time."""
        remaining = deadline - clock()
        if remaining <= 0:
            raise SyncFailed(SyncFailure.TIMEOUT)
        return min(REQUEST_SECONDS, remaining)

    documents: list[ListedDocument] = []
    seen: set[str] = set()
    cursors: set[str] = set()
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        raw = client.list_page(cursor, timeout=budget())
        budget()
        if len(raw) > MAX_PAGE_BYTES:
            raise SyncFailed(SyncFailure.LISTING_LIMIT)
        try:
            page = parse_listing_page(raw)
        except ListingRejected:
            raise SyncFailed(SyncFailure.INVALID_LISTING) from None
        for entry in page.documents:
            if entry.external_id in seen:
                raise SyncFailed(SyncFailure.INVALID_LISTING)
            seen.add(entry.external_id)
            documents.append(entry)
        if len(documents) > MAX_SOURCE_DOCUMENTS:
            raise SyncFailed(SyncFailure.LISTING_LIMIT)
        if page.next_cursor is None:
            break
        if page.next_cursor in cursors:
            raise SyncFailed(SyncFailure.INVALID_LISTING)
        cursors.add(page.next_cursor)
        cursor = page.next_cursor
    else:
        raise SyncFailed(SyncFailure.LISTING_LIMIT)
    contents: dict[str, str | TextRejection] = {}
    for entry in documents:
        if needs(entry):
            raw = client.fetch_content(
                entry.external_id, entry.revision, timeout=budget()
            )
            budget()
            try:
                contents[entry.external_id] = decode_text(raw)
            except TextRejected as rejection:
                contents[entry.external_id] = rejection.reason
    budget()
    return Snapshot(documents=tuple(documents), contents=contents)


class SyncOutcome(StrEnum):
    """Say whether a complete snapshot was applied."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SyncCounts:
    """Count what one sync read and changed, without names or text."""

    listed: int = 0
    fetched: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    restored: int = 0
    deleted: int = 0
    withheld: int = 0
    unmapped_readers: int = 0
    grants_added: int = 0
    grants_revoked: int = 0


@dataclass(frozen=True, slots=True)
class SyncReport:
    """Report one sync run; withheld lists (external ID, rejection code) pairs."""

    run_id: str
    source_id: str
    outcome: SyncOutcome
    failure: SyncFailure | None = None
    counts: SyncCounts = SyncCounts()
    withheld: tuple[tuple[str, str], ...] = ()
