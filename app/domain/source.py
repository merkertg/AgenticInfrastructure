"""Validate one read-only document source and plan how a snapshot changes memory.

Listing pages and document texts are untrusted connector payloads. They are
parsed strictly, never followed as addresses and can only give read access to
employees whom trusted setup mapped from source accounts.
"""

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from app.domain.strict_json import loads_strict
from app.domain.text import TextRejection

SOURCE_KIND = "handbook-http-v1"
MAX_PAGE_BYTES = 262_144
MAX_PAGES = 20
MAX_PAGE_DOCUMENTS = 100
MAX_SOURCE_DOCUMENTS = 200
MAX_READERS = 100
MIN_MAX_AGE_SECONDS = 60
MAX_MAX_AGE_SECONDS = 86_400
GRANT_NAMESPACE = uuid5(NAMESPACE_URL, "urn:agentic-infrastructure:source-grant")
_MEMORY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_SOURCE_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")
_SPACE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_PREFIX = re.compile(r"[a-z][a-z0-9-]{0,30}-")
_EXTERNAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_REVISION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
_PRINCIPAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}")
_READER_KIND = re.compile(r"[a-z][a-z_]{0,31}")
_CURSOR = re.compile(r"[A-Za-z0-9_.~=+/-]{1,512}")
_PATH = re.compile(r"(?:/[A-Za-z0-9_.~-]+)*")
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
_PAGE_KEYS = frozenset({"documents", "next_cursor"})
_DOCUMENT_KEYS = frozenset({"id", "title", "revision", "readers"})
_READER_KEYS = frozenset({"kind", "id"})


def _matches(pattern: re.Pattern[str], value: object) -> bool:
    """Tell whether a value is a string that fully matches the pattern."""
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _is_label(value: object) -> bool:
    """Accept printable, trimmed single-line metadata of 1 to 256 characters."""
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 256
        and value == value.strip()
        and value.isprintable()
    )


def check_base_url(value: str) -> str:
    """Accept HTTPS, or HTTP to loopback for the lab, without credentials or query.

    Request paths are built by the connector from validated identifiers, so the
    base URL is the only address a source can ever reach.
    """
    if not isinstance(value, str):
        raise TypeError("base_url must be a string")
    if (
        not 1 <= len(value) <= 256
        or not value.isascii()
        or any(character.isspace() for character in value)
    ):
        raise ValueError("base_url must be 1 to 256 ASCII characters without spaces")
    parts = urlsplit(value)
    try:
        port = parts.port
    except ValueError:
        raise ValueError("base_url has an invalid port") from None
    if port == 0:
        raise ValueError("base_url has an invalid port")
    if (
        parts.scheme not in {"https", "http"}
        or not parts.hostname
        or "@" in parts.netloc
        or parts.query
        or parts.fragment
        or value.endswith(("?", "#"))
        or _PATH.fullmatch(parts.path) is None
    ):
        raise ValueError("base_url must be http(s)://host[:port][/path] only")
    if parts.scheme == "http" and parts.hostname not in _LOOPBACK:
        raise ValueError("plain HTTP is allowed only for a loopback lab source")
    return value


@dataclass(frozen=True, slots=True)
class SourceSettings:
    """Fix one source's address, space, target project and freshness window."""

    id: str
    company_id: str
    project_id: str
    base_url: str
    space: str
    document_prefix: str
    max_age_seconds: int

    def __post_init__(self) -> None:
        """Reject settings that could reach another address or exceed the limits."""
        if not _matches(_SOURCE_ID, self.id):
            raise ValueError("id must be 1 to 64 lowercase letters, digits or -")
        if not _matches(_MEMORY_ID, self.company_id) or not _matches(
            _MEMORY_ID, self.project_id
        ):
            raise ValueError("company_id and project_id must be valid identifiers")
        check_base_url(self.base_url)
        if not _matches(_SPACE, self.space):
            raise ValueError("space must be a valid source identifier")
        if not _matches(_PREFIX, self.document_prefix):
            raise ValueError("document_prefix must look like 'hb-' (2 to 32 chars)")
        if (
            type(self.max_age_seconds) is not int
            or not MIN_MAX_AGE_SECONDS <= self.max_age_seconds <= MAX_MAX_AGE_SECONDS
        ):
            raise ValueError("max_age_seconds must be an integer from 60 to 86400")


def check_principal(value: str) -> str:
    """Require a source user identifier usable in an account mapping."""
    if not _matches(_PRINCIPAL, value):
        raise ValueError("principal must be a valid source user identifier")
    return value


def source_confirmed(
    confirmed_at: datetime | None, max_age_seconds: int, occurred_at: datetime
) -> bool:
    """Tell whether the last complete snapshot still vouches for current access."""
    if confirmed_at is None:
        return False
    return occurred_at < confirmed_at + timedelta(seconds=max_age_seconds)


def memory_document_id(prefix: str, external_id: str) -> str:
    """Name the memory document that mirrors one source document."""
    return prefix + external_id


def source_grant_id(company_id: str, document_id: str, employee_id: str) -> str:
    """Name the read grant a sync owns for one employee and synced document."""
    name = f"{company_id}/{document_id}/{employee_id}"
    return f"source-grant-{uuid5(GRANT_NAMESPACE, name)}"


def version_label(source_id: str, external_id: str, revision: str) -> str:
    """Describe where a synced version came from, for citations and history."""
    return f"{source_id}:{external_id}@{revision}"


def content_digest(text: str) -> str:
    """Fingerprint imported text so an unchanged revision creates no version."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ListingRejected(ValueError):
    """Signal a listing page that breaks the source contract; nothing is echoed."""


@dataclass(frozen=True, slots=True)
class ListedDocument:
    """Describe one listed source document and the user principals that may read."""

    external_id: str
    title: str
    revision: str
    principals: frozenset[str]
    # Readers of other kinds, such as groups, are not resolved and grant nothing.
    ignored_readers: int = 0


@dataclass(frozen=True, slots=True)
class ListingPage:
    """Hold one validated page of the listing and the opaque cursor to the next."""

    documents: tuple[ListedDocument, ...]
    next_cursor: str | None


def _parse_document(value: object) -> ListedDocument:
    """Validate one listing entry exactly as the source contract describes it."""
    if not isinstance(value, dict) or set(value) != _DOCUMENT_KEYS:
        raise ListingRejected("document entry has unexpected fields")
    readers = value["readers"]
    if (
        not _matches(_EXTERNAL_ID, value["id"])
        or not _is_label(value["title"])
        or not _matches(_REVISION, value["revision"])
        or not isinstance(readers, list)
        or len(readers) > MAX_READERS
    ):
        raise ListingRejected("document entry has invalid values")
    principals: set[str] = set()
    ignored = 0
    for reader in readers:
        if not isinstance(reader, dict) or set(reader) != _READER_KEYS:
            raise ListingRejected("reader entry has unexpected fields")
        kind, identifier = reader["kind"], reader["id"]
        if kind == "user" and _matches(_PRINCIPAL, identifier):
            principals.add(identifier)
        elif (
            kind != "user"
            and _matches(_READER_KIND, kind)
            and isinstance(identifier, str)
            and 1 <= len(identifier) <= 256
        ):
            ignored += 1
        else:
            raise ListingRejected("reader entry has invalid values")
    return ListedDocument(
        external_id=value["id"],
        title=value["title"],
        revision=value["revision"],
        principals=frozenset(principals),
        ignored_readers=ignored,
    )


def parse_listing_page(raw: bytes) -> ListingPage:
    """Parse one strict JSON listing page; any contract violation rejects it all."""
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    if len(raw) > MAX_PAGE_BYTES:
        raise ListingRejected("page is too large")
    try:
        values = loads_strict(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ListingRejected("page is not strict UTF-8 JSON") from None
    if not isinstance(values, dict) or set(values) != _PAGE_KEYS:
        raise ListingRejected("page has unexpected fields")
    documents, cursor = values["documents"], values["next_cursor"]
    if not isinstance(documents, list) or len(documents) > MAX_PAGE_DOCUMENTS:
        raise ListingRejected("page documents must be a bounded list")
    if cursor is not None and not _matches(_CURSOR, cursor):
        raise ListingRejected("next_cursor must be null or an opaque token")
    return ListingPage(
        documents=tuple(_parse_document(entry) for entry in documents),
        next_cursor=cursor,
    )


class DocumentState(StrEnum):
    """Say whether a synced document is readable, deleted or held back."""

    ACTIVE = "active"
    DELETED = "deleted"
    WITHHELD = "withheld"


@dataclass(frozen=True, slots=True)
class StoredDocument:
    """Describe what memory holds for one source document before a sync."""

    external_id: str
    document_id: str
    title: str
    # The last imported revision and its digest; None until text is imported.
    revision: str | None
    content_sha256: str | None
    state: DocumentState


class ChangeKind(StrEnum):
    """Name the effect of one sync on one document."""

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    DELETED = "deleted"
    WITHHELD = "withheld"


@dataclass(frozen=True, slots=True)
class DocumentChange:
    """Describe a document after the sync; content is set only for a new version."""

    external_id: str
    document_id: str
    kind: ChangeKind
    title: str
    revision: str | None
    content_sha256: str | None
    readers: frozenset[str]
    content: str | None = field(default=None, repr=False)
    withheld_reason: str | None = None
    restored: bool = False

    @property
    def available(self) -> bool:
        """Tell whether the document is readable after the sync."""
        return self.kind not in {ChangeKind.DELETED, ChangeKind.WITHHELD}


@dataclass(frozen=True, slots=True)
class SyncPlan:
    """List every document change of one complete snapshot."""

    changes: tuple[DocumentChange, ...]
    unmapped_readers: int

    def count(self, kind: ChangeKind) -> int:
        """Count changes of one kind for the sync report."""
        return sum(1 for change in self.changes if change.kind is kind)


def needs_content(stored: StoredDocument | None, listed: ListedDocument) -> bool:
    """Fetch text only for a new document or a revision memory has not imported."""
    return stored is None or stored.revision != listed.revision


def _listed_change(
    prefix: str,
    stored: StoredDocument | None,
    listed: ListedDocument,
    content: str | TextRejection | None,
    readers: frozenset[str],
) -> DocumentChange:
    """Decide the effect of one listed document from its stored state and text."""
    document_id = (
        stored.document_id
        if stored is not None
        else memory_document_id(prefix, listed.external_id)
    )
    unavailable = stored is not None and stored.state is not DocumentState.ACTIVE
    if isinstance(content, TextRejection):
        return DocumentChange(
            external_id=listed.external_id,
            document_id=document_id,
            kind=ChangeKind.WITHHELD,
            title=listed.title,
            revision=stored.revision if stored is not None else None,
            content_sha256=stored.content_sha256 if stored is not None else None,
            readers=frozenset(),
            withheld_reason=content.value,
        )
    digest = content_digest(content) if content is not None else None
    if content is None or (stored is not None and stored.content_sha256 == digest):
        assert stored is not None
        return DocumentChange(
            external_id=listed.external_id,
            document_id=document_id,
            kind=ChangeKind.UNCHANGED,
            title=listed.title,
            revision=listed.revision,
            content_sha256=stored.content_sha256,
            readers=readers,
            restored=unavailable,
        )
    first = stored is None or stored.content_sha256 is None
    return DocumentChange(
        external_id=listed.external_id,
        document_id=document_id,
        kind=ChangeKind.CREATED if first else ChangeKind.UPDATED,
        title=listed.title,
        revision=listed.revision,
        content_sha256=digest,
        readers=readers,
        content=content,
        restored=unavailable and not first,
    )


def plan_sync(
    *,
    prefix: str,
    stored: Mapping[str, StoredDocument],
    listed: tuple[ListedDocument, ...],
    contents: Mapping[str, str | TextRejection],
    accounts: Mapping[str, str],
) -> SyncPlan:
    """Compare a complete listing with memory; absent documents count as deleted.

    Only mapped user principals become readers, and unavailable documents keep
    no readers, so a sync can never leave access that the source no longer gives.
    """
    if len({entry.external_id for entry in listed}) != len(listed):
        raise ValueError("listed documents must have unique identifiers")
    changes: list[DocumentChange] = []
    unmapped = 0
    for entry in listed:
        previous = stored.get(entry.external_id)
        content = contents.get(entry.external_id)
        if needs_content(previous, entry) and content is None:
            raise ValueError("content is required for a new or changed revision")
        mapped = frozenset(
            accounts[principal]
            for principal in entry.principals
            if principal in accounts
        )
        unmapped += entry.ignored_readers + sum(
            1 for principal in entry.principals if principal not in accounts
        )
        changes.append(_listed_change(prefix, previous, entry, content, mapped))
    present = {entry.external_id for entry in listed}
    for external_id, previous in sorted(stored.items()):
        if external_id not in present and previous.state is not DocumentState.DELETED:
            changes.append(
                DocumentChange(
                    external_id=external_id,
                    document_id=previous.document_id,
                    kind=ChangeKind.DELETED,
                    title=previous.title,
                    revision=previous.revision,
                    content_sha256=previous.content_sha256,
                    readers=frozenset(),
                )
            )
    return SyncPlan(changes=tuple(changes), unmapped_readers=unmapped)
