"""Check source settings, strict listing parsing, freshness and sync planning."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.domain.source import (
    MAX_PAGE_BYTES,
    MAX_PAGE_DOCUMENTS,
    MAX_READERS,
    ChangeKind,
    DocumentState,
    ListedDocument,
    ListingRejected,
    SourceSettings,
    StoredDocument,
    check_base_url,
    check_principal,
    content_digest,
    needs_content,
    parse_listing_page,
    plan_sync,
    source_confirmed,
    source_grant_id,
    version_label,
)
from app.domain.text import TextRejection

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
SETTINGS = SourceSettings(
    id="handbook",
    company_id="alpha",
    project_id="finance",
    base_url="https://handbook.alpha.example/api",
    space="alpha-handbook",
    document_prefix="hb-",
    max_age_seconds=900,
)


def _page(documents: list[Any], cursor: str | None = None) -> bytes:
    """Encode a listing page as the source would send it."""
    return json.dumps({"documents": documents, "next_cursor": cursor}).encode()


def _entry(**overrides: Any) -> dict[str, Any]:
    """Build one valid listing entry, optionally replacing fields."""
    entry: dict[str, Any] = {
        "id": "travel",
        "title": "Travel rules",
        "revision": "r1",
        "readers": [{"kind": "user", "id": "u-fin"}],
    }
    entry.update(overrides)
    return entry


@pytest.mark.parametrize(
    "url",
    [
        "https://handbook.alpha.example",
        "https://handbook.alpha.example:8443/api/v2",
        "http://127.0.0.1:8105",
        "http://localhost:8105/base",
        "http://[::1]:8105",
    ],
)
def test_allowed_base_urls(url: str) -> None:
    """HTTPS hosts and plain HTTP on loopback are the only reachable addresses."""
    assert check_base_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://handbook.alpha.example",
        "ftp://handbook.alpha.example",
        "https://user:secret@handbook.alpha.example",
        "https://handbook.alpha.example/api?token=x",
        "https://handbook.alpha.example/api#part",
        "https://handbook.alpha.example/api/",
        "https://handbook.alpha.example/a%2Fb",
        "https://handbook.alpha.example:0",
        "https://handbook.alpha.example:99999",
        "https:///path-only",
        "https://handbook.alpha.example/ api",
        "https://handbook.alpha.exämple",
        "https://" + "a" * 260,
        "https://handbook.alpha.example?",
    ],
)
def test_rejected_base_urls(url: str) -> None:
    """Credentials, queries, other schemes and odd paths never become addresses."""
    with pytest.raises(ValueError):
        check_base_url(url)


def test_base_url_requires_a_string() -> None:
    """A non-string address is a programming error, not a value to coerce."""
    with pytest.raises(TypeError):
        check_base_url(b"https://example")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field, value",
    [
        ("id", "Handbook"),
        ("id", ""),
        ("company_id", "bad company"),
        ("project_id", ""),
        ("base_url", "http://example.com"),
        ("space", "../other"),
        ("document_prefix", "hb"),
        ("document_prefix", "HB-"),
        ("max_age_seconds", 59),
        ("max_age_seconds", 86_401),
        ("max_age_seconds", True),
    ],
)
def test_source_settings_reject_invalid_values(field: str, value: object) -> None:
    """Settings are validated before anything is stored or contacted."""
    with pytest.raises(ValueError):
        changes: dict[str, Any] = {field: value}
        replace(SETTINGS, **changes)


def test_principals_are_checked() -> None:
    """Account mappings accept source user IDs only."""
    assert check_principal("u-fin@alpha") == "u-fin@alpha"
    for value in ("", " u", "u fin", "x" * 129):
        with pytest.raises(ValueError):
            check_principal(value)


def test_listing_page_is_parsed() -> None:
    """A valid page yields documents, user principals and the next cursor."""
    page = parse_listing_page(
        _page(
            [
                _entry(
                    readers=[
                        {"kind": "user", "id": "u-fin"},
                        {"kind": "user", "id": "u-fin"},
                        {"kind": "group", "id": "everyone"},
                    ]
                )
            ],
            "p1",
        )
    )
    assert page.next_cursor == "p1"
    assert page.documents == (
        ListedDocument(
            external_id="travel",
            title="Travel rules",
            revision="r1",
            principals=frozenset({"u-fin"}),
            ignored_readers=1,
        ),
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff",
        b"[]",
        b'{"documents": [], "next_cursor": null, "extra": 1}',
        b'{"documents": [], "documents": [], "next_cursor": null}',
        b'{"documents": NaN, "next_cursor": null}',
        b'{"documents": {}, "next_cursor": null}',
        b'{"documents": [], "next_cursor": "has space"}',
        b'{"documents": [], "next_cursor": 1}',
        b'{"documents": [], "next_cursor": ""}',
        b"[" * 5000 + b"]" * 5000,
    ],
)
def test_malformed_pages_are_rejected(raw: bytes) -> None:
    """Pages that break the contract are refused without echoing their bytes."""
    with pytest.raises(ListingRejected):
        parse_listing_page(raw)


@pytest.mark.parametrize(
    "entry",
    [
        _entry(id="../etc"),
        _entry(id="x" * 65),
        _entry(title=" padded"),
        _entry(title="two\nlines"),
        _entry(title=""),
        _entry(revision="r 1"),
        _entry(readers="u-fin"),
        _entry(readers=[{"kind": "user"}]),
        _entry(readers=[{"kind": "user", "id": "u fin"}]),
        _entry(readers=[{"kind": "user", "id": 7}]),
        _entry(readers=[{"kind": "Group", "id": "g"}]),
        _entry(readers=[{"kind": "group", "id": ""}]),
        _entry(readers=[{"kind": "user", "id": "u", "role": "admin"}]),
        _entry(readers=[{"kind": "user", "id": "u"}] * (MAX_READERS + 1)),
        _entry(project_id="engineering"),
        {"id": "travel"},
        "travel",
    ],
)
def test_malformed_entries_reject_the_page(entry: Any) -> None:
    """A single bad entry, including a scope field, rejects the whole page."""
    with pytest.raises(ListingRejected):
        parse_listing_page(_page([entry]))


def test_page_limits_are_enforced() -> None:
    """Pages with too many entries or bytes are refused."""
    entries = [_entry(id=f"doc-{index}") for index in range(MAX_PAGE_DOCUMENTS + 1)]
    with pytest.raises(ListingRejected):
        parse_listing_page(_page(entries))
    with pytest.raises(ListingRejected):
        parse_listing_page(b" " * (MAX_PAGE_BYTES + 1))
    with pytest.raises(TypeError):
        parse_listing_page("{}")  # type: ignore[arg-type]


def test_freshness_window() -> None:
    """A snapshot vouches for access strictly inside its window only."""
    assert not source_confirmed(None, 900, NOW)
    assert source_confirmed(NOW, 900, NOW)
    assert source_confirmed(NOW, 900, NOW + timedelta(seconds=899))
    assert not source_confirmed(NOW, 900, NOW + timedelta(seconds=900))
    # A request timed just before the sync started is still inside the window.
    assert source_confirmed(NOW, 900, NOW - timedelta(seconds=1))


def test_identifiers_are_deterministic() -> None:
    """Grant IDs and version labels are stable and scoped."""
    grant = source_grant_id("alpha", "hb-travel", "fin-user")
    assert grant == source_grant_id("alpha", "hb-travel", "fin-user")
    assert grant != source_grant_id("alpha", "hb-travel", "eng-user")
    assert grant.startswith("source-grant-") and len(grant) <= 128
    assert version_label("handbook", "travel", "r2") == "handbook:travel@r2"


def _listed(
    external_id: str = "travel",
    revision: str = "r1",
    principals: frozenset[str] = frozenset({"u-fin"}),
    ignored: int = 0,
) -> ListedDocument:
    """Build a parsed listing entry for planning tests."""
    return ListedDocument(
        external_id=external_id,
        title=f"Title {external_id}",
        revision=revision,
        principals=principals,
        ignored_readers=ignored,
    )


def _stored(
    external_id: str = "travel",
    revision: str | None = "r1",
    text: str | None = "Old text.",
    state: DocumentState = DocumentState.ACTIVE,
) -> StoredDocument:
    """Build stored state for planning tests."""
    return StoredDocument(
        external_id=external_id,
        document_id=f"hb-{external_id}",
        title=f"Title {external_id}",
        revision=revision,
        content_sha256=content_digest(text) if text is not None else None,
        state=state,
    )


ACCOUNTS = {"u-fin": "fin-user", "u-eng": "eng-user"}


def _plan(
    stored: dict[str, StoredDocument],
    listed: tuple[ListedDocument, ...],
    contents: dict[str, str | TextRejection],
) -> Any:
    """Plan a sync with the fixed prefix and accounts."""
    return plan_sync(
        prefix="hb-", stored=stored, listed=listed, contents=contents, accounts=ACCOUNTS
    )


def test_new_document_is_created_with_mapped_readers_only() -> None:
    """Unmapped users and other reader kinds are counted but grant nothing."""
    plan = _plan(
        {},
        (_listed(principals=frozenset({"u-fin", "u-guest"}), ignored=2),),
        {"travel": "New text."},
    )
    (change,) = plan.changes
    assert change.kind is ChangeKind.CREATED
    assert change.document_id == "hb-travel"
    assert change.readers == frozenset({"fin-user"})
    assert change.content == "New text."
    assert change.content_sha256 == content_digest("New text.")
    assert plan.unmapped_readers == 3
    assert "New text." not in repr(change)


def test_same_revision_needs_no_text_and_keeps_content() -> None:
    """An already imported revision is not fetched and creates no version."""
    stored = _stored()
    listed = _listed()
    assert not needs_content(stored, listed)
    assert needs_content(None, listed)
    assert needs_content(stored, replace(listed, revision="r2"))
    (change,) = _plan({"travel": stored}, (listed,), {}).changes
    assert change.kind is ChangeKind.UNCHANGED
    assert change.content is None and not change.restored


def test_new_revision_with_new_text_updates() -> None:
    """Changed text becomes a new version; same text under a new revision does not."""
    stored = {"travel": _stored()}
    (updated,) = _plan(stored, (_listed(revision="r2"),), {"travel": "New."}).changes
    assert updated.kind is ChangeKind.UPDATED
    assert updated.revision == "r2" and updated.content == "New."
    (same,) = _plan(stored, (_listed(revision="r2"),), {"travel": "Old text."}).changes
    assert same.kind is ChangeKind.UNCHANGED
    assert same.revision == "r2" and same.content is None


def test_missing_document_is_deleted_without_readers() -> None:
    """Absence from a complete listing deletes; deleted stays deleted quietly."""
    stored = {
        "travel": _stored(),
        "gone": _stored("gone", state=DocumentState.DELETED),
    }
    (change,) = _plan(stored, (), {}).changes
    assert change.kind is ChangeKind.DELETED
    assert change.external_id == "travel"
    assert change.readers == frozenset() and not change.available


def test_deleted_document_can_be_restored() -> None:
    """A document listed again becomes available, with or without new text."""
    stored = {"travel": _stored(state=DocumentState.DELETED)}
    (same,) = _plan(stored, (_listed(),), {}).changes
    assert same.kind is ChangeKind.UNCHANGED and same.restored and same.available
    (changed,) = _plan(stored, (_listed(revision="r2"),), {"travel": "New."}).changes
    assert changed.kind is ChangeKind.UPDATED and changed.restored


def test_rejected_text_withholds_the_document() -> None:
    """Text that fails intake rules makes the document unavailable, new or old."""
    (new,) = _plan({}, (_listed(),), {"travel": TextRejection.NOT_UTF8}).changes
    assert new.kind is ChangeKind.WITHHELD
    assert new.withheld_reason == "not_utf8"
    assert new.revision is None and new.readers == frozenset()
    stored = {"travel": _stored()}
    (old,) = _plan(
        stored, (_listed(revision="r2"),), {"travel": TextRejection.TOO_LARGE}
    ).changes
    assert old.kind is ChangeKind.WITHHELD
    # Memory still records the last imported revision, so r2 is fetched again.
    assert old.revision == "r1" and not old.available


def test_withheld_document_without_text_gets_its_first_version() -> None:
    """A document that never had valid text is created, not restored."""
    stored = {"travel": _stored(revision=None, text=None, state=DocumentState.WITHHELD)}
    (change,) = _plan(stored, (_listed(revision="r2"),), {"travel": "Valid."}).changes
    assert change.kind is ChangeKind.CREATED and not change.restored


def test_planning_requires_needed_texts_and_unique_entries() -> None:
    """A missing text or a duplicate entry is a caller error."""
    with pytest.raises(ValueError):
        _plan({}, (_listed(),), {})
    with pytest.raises(ValueError):
        _plan({}, (_listed(), _listed()), {"travel": "Text."})


def test_counts_by_kind() -> None:
    """Plans count their changes for the run report."""
    plan = _plan(
        {"gone": _stored("gone")},
        (_listed("a"), _listed("b")),
        {"a": "A.", "b": TextRejection.EMPTY},
    )
    assert plan.count(ChangeKind.CREATED) == 1
    assert plan.count(ChangeKind.WITHHELD) == 1
    assert plan.count(ChangeKind.DELETED) == 1
