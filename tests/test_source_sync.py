"""Check that a sync collects a complete bounded snapshot or nothing at all."""

import json
from collections.abc import Callable
from typing import Any

import pytest

from app.application.source_sync import (
    REQUEST_SECONDS,
    SyncFailed,
    SyncFailure,
    collect_snapshot,
)
from app.domain.source import MAX_PAGE_BYTES, MAX_PAGES, MAX_SOURCE_DOCUMENTS
from app.domain.text import MAX_BYTES, TextRejection


def _entry(external_id: str, revision: str = "r1") -> dict[str, Any]:
    """Build one listing entry readable by a fictional user."""
    return {
        "id": external_id,
        "title": f"Title {external_id}",
        "revision": revision,
        "readers": [{"kind": "user", "id": "u-fin"}],
    }


class FakeSource:
    """Serve scripted pages and texts and record every request."""

    def __init__(
        self,
        pages: dict[str | None, tuple[list[Any], str | None]],
        texts: dict[str, bytes] | None = None,
    ) -> None:
        """Keep pages keyed by cursor and texts keyed by document ID."""
        self.pages = pages
        self.texts = texts or {}
        self.calls: list[tuple[str, object, float]] = []
        self.raw_page: bytes | None = None
        self.failure: SyncFailure | None = None

    def list_page(self, cursor: str | None, *, timeout: float) -> bytes:
        """Return the scripted page for the cursor."""
        self.calls.append(("page", cursor, timeout))
        if self.failure is not None:
            raise SyncFailed(self.failure)
        if self.raw_page is not None:
            return self.raw_page
        documents, following = self.pages[cursor]
        return json.dumps({"documents": documents, "next_cursor": following}).encode()

    def fetch_content(
        self, external_id: str, revision: str, *, timeout: float
    ) -> bytes:
        """Return the scripted text of one document."""
        self.calls.append(("text", external_id, timeout))
        return self.texts[external_id]


def _clock(values: list[float]) -> Callable[[], float]:
    """Return a monotonic clock that walks through the given readings."""
    readings = iter(values)
    last = [0.0]

    def now() -> float:
        """Advance to the next reading, repeating the last one at the end."""
        last[0] = next(readings, last[0])
        return last[0]

    return now


def _collect(
    source: FakeSource,
    needs: Callable[[Any], bool] = lambda entry: True,
    *,
    deadline: float = 60.0,
    clock: Callable[[], float] | None = None,
) -> Any:
    """Collect with a frozen clock unless one is given."""
    return collect_snapshot(
        source, needs, deadline=deadline, clock=clock or (lambda: 0.0)
    )


def test_all_pages_and_needed_texts_are_collected() -> None:
    """Pages are followed by cursor, and only selected texts are fetched."""
    source = FakeSource(
        {None: ([_entry("a"), _entry("b")], "p1"), "p1": ([_entry("c")], None)},
        {"a": b"Text a.", "c": b"\xff"},
    )
    snapshot = _collect(source, lambda entry: entry.external_id != "b")
    assert [entry.external_id for entry in snapshot.documents] == ["a", "b", "c"]
    assert snapshot.contents == {"a": "Text a.", "c": TextRejection.NOT_UTF8}
    assert [call[:2] for call in source.calls] == [
        ("page", None),
        ("page", "p1"),
        ("text", "a"),
        ("text", "c"),
    ]
    assert all(call[2] == REQUEST_SECONDS for call in source.calls)
    assert "Text a." not in repr(snapshot)


def test_text_limits_are_document_rejections() -> None:
    """Oversized, empty or control-character texts withhold only that document."""
    source = FakeSource(
        {None: ([_entry("big"), _entry("empty"), _entry("bell")], None)},
        {"big": b"x" * (MAX_BYTES + 1), "empty": b"  \n", "bell": b"a\x07b"},
    )
    assert _collect(source).contents == {
        "big": TextRejection.TOO_LARGE,
        "empty": TextRejection.EMPTY,
        "bell": TextRejection.CONTROL_CHARACTERS,
    }


@pytest.mark.parametrize(
    "pages",
    [
        # A cursor that points back to an earlier page.
        {None: ([_entry("a")], "p1"), "p1": ([_entry("b")], "p1")},
        # The same document on two pages.
        {None: ([_entry("a")], "p1"), "p1": ([_entry("a", "r2")], None)},
        # A malformed entry.
        {None: ([{"id": "a"}], None)},
    ],
)
def test_inconsistent_listings_fail(pages: dict[str | None, Any]) -> None:
    """Loops, duplicates and contract violations reject the whole listing."""
    with pytest.raises(SyncFailed) as raised:
        _collect(FakeSource(pages))
    assert raised.value.failure is SyncFailure.INVALID_LISTING


def test_page_and_document_limits_fail() -> None:
    """A listing larger than the limits cannot prove which documents are gone."""
    endless: dict[str | None, tuple[list[Any], str | None]] = {
        None if index == 0 else f"p{index}": ([], f"p{index + 1}")
        for index in range(MAX_PAGES + 1)
    }
    with pytest.raises(SyncFailed) as raised:
        _collect(FakeSource(endless))
    assert raised.value.failure is SyncFailure.LISTING_LIMIT

    many: dict[str | None, tuple[list[Any], str | None]] = {
        None if index == 0 else f"p{index}": (
            [_entry(f"d{index}-{item}") for item in range(100)],
            f"p{index + 1}" if index < 2 else None,
        )
        for index in range(3)
    }
    assert 300 > MAX_SOURCE_DOCUMENTS
    with pytest.raises(SyncFailed) as raised:
        _collect(FakeSource(many))
    assert raised.value.failure is SyncFailure.LISTING_LIMIT

    oversized = FakeSource({})
    oversized.raw_page = b" " * (MAX_PAGE_BYTES + 1)
    with pytest.raises(SyncFailed) as raised:
        _collect(oversized)
    assert raised.value.failure is SyncFailure.LISTING_LIMIT


def test_deadline_bounds_every_request() -> None:
    """Each request gets the remaining time, and an expired deadline stops the sync."""
    source = FakeSource(
        {None: ([_entry("a"), _entry("b")], None)},
        {"a": b"A.", "b": b"B."},
    )
    with pytest.raises(SyncFailed) as raised:
        _collect(source, deadline=10.0, clock=_clock([0.0, 0.0, 8.0, 10.0]))
    assert raised.value.failure is SyncFailure.TIMEOUT
    assert [(call[0], call[2]) for call in source.calls] == [
        ("page", REQUEST_SECONDS),
        ("text", 2.0),
    ]


@pytest.mark.parametrize("finished_at", [10.0, 11.0])
@pytest.mark.parametrize("last_request", ["page", "text"])
def test_deadline_is_checked_after_the_last_request(
    last_request: str, finished_at: float
) -> None:
    """A final page or text arriving at or after the deadline is never applied."""
    if last_request == "page":
        source = FakeSource({None: ([], None)})
        readings = [0.0, finished_at]
    else:
        source = FakeSource({None: ([_entry("a")], None)}, {"a": b"A."})
        readings = [0.0, 0.0, 0.0, finished_at]
    with pytest.raises(SyncFailed) as raised:
        _collect(source, deadline=10.0, clock=_clock(readings))
    assert raised.value.failure is SyncFailure.TIMEOUT
    assert source.calls[-1][0] == last_request


def test_client_failures_propagate() -> None:
    """Transport failures from the client stop the sync with their code."""
    source = FakeSource({})
    source.failure = SyncFailure.UNAVAILABLE
    with pytest.raises(SyncFailed) as raised:
        _collect(source)
    assert raised.value.failure is SyncFailure.UNAVAILABLE
    assert str(raised.value) == "unavailable"
