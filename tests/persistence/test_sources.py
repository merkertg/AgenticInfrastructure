"""Verify source sync, its access effects and its history on actual PostgreSQL."""

import json
import logging
import threading
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.application.agent import RunOutcome, run_agent
from app.application.context import ContextResult
from app.application.offline_model import OfflineProvider
from app.application.source_sync import (
    Snapshot,
    SyncFailed,
    SyncFailure,
    SyncOutcome,
    SyncReport,
)
from app.config import Settings
from app.connectors.handbook import HandbookClient
from app.domain.models import (
    AccessRequest,
    Action,
    DocumentVersion,
    ReasonCode,
    ScopeRequest,
)
from app.domain.source import ListedDocument, SourceSettings
from app.domain.versions import derive_version_id
from app.persistence import schema
from app.persistence import sources as source_store
from app.persistence.credentials import issue_credential
from app.persistence.intake import IntakeGrant, IntakeItem, ingest_documents
from app.persistence.sources import (
    apply_snapshot,
    register_source,
    sync_status,
    synchronize,
)
from app.source_lab import LabServer, load_snapshot
from tests.persistence.conftest import TIMESTAMP, PersistenceCase, row_count

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "lab-token-0123456789abcdef"
SYNC_AT = TIMESTAMP - timedelta(minutes=1)
EXPIRES = SYNC_AT + timedelta(seconds=900)
TRAVEL = "Train tickets are booked two days ahead.\n\nTaxi rides need a receipt."
OFFICE = "Visitor badges are issued at the front desk."
PARTNER = "Partner badges note for an unmapped account."
SETTINGS = SourceSettings(
    id="handbook",
    company_id="alpha",
    project_id="finance",
    base_url="https://handbook.alpha.example",
    space="alpha-handbook",
    document_prefix="hb-",
    max_age_seconds=900,
)
ACCOUNTS = {"u-fin": "employee-finance", "u-aud": "employee-auditor"}
SOURCE_TABLES = (
    schema.sources,
    schema.source_accounts,
    schema.source_documents,
    schema.source_sync_runs,
)


class FakeSource:
    """Serve a mutable in-memory source the way the connector would."""

    def __init__(self) -> None:
        """Start empty with a small page size so paging is exercised."""
        self.documents: dict[str, dict[str, Any]] = {}
        self.failure: SyncFailure | None = None
        self.broken_page: int | None = None
        self.fetched: list[str] = []

    def put(
        self,
        external_id: str,
        text: str | bytes,
        *,
        revision: str = "r1",
        readers: tuple[str, ...] = ("u-fin",),
        groups: int = 0,
    ) -> None:
        """Create or replace one source document."""
        self.documents[external_id] = {
            "title": f"Title {external_id}",
            "revision": revision,
            "readers": [{"kind": "user", "id": reader} for reader in readers]
            + [{"kind": "group", "id": f"g-{index}"} for index in range(groups)],
            "raw": text if isinstance(text, bytes) else text.encode(),
        }

    def list_page(self, cursor: str | None, *, timeout: float) -> bytes:
        """Return two documents per page in ID order."""
        if self.failure is not None:
            raise SyncFailed(self.failure)
        index = int(cursor or 0)
        if index == self.broken_page:
            return b'{"documents": "broken", "next_cursor": null}'
        names = sorted(self.documents)
        chunk = names[index * 2 : index * 2 + 2]
        return json.dumps(
            {
                "documents": [
                    {
                        "id": name,
                        "title": self.documents[name]["title"],
                        "revision": self.documents[name]["revision"],
                        "readers": self.documents[name]["readers"],
                    }
                    for name in chunk
                ],
                "next_cursor": str(index + 1) if index * 2 + 2 < len(names) else None,
            }
        ).encode()

    def fetch_content(
        self, external_id: str, revision: str, *, timeout: float
    ) -> bytes:
        """Return the text of the listed revision."""
        document = self.documents[external_id]
        assert document["revision"] == revision
        self.fetched.append(external_id)
        return bytes(document["raw"])


def _add_actor(case: PersistenceCase, employee_id: str, agent_id: str) -> None:
    """Register another employee with a read-only finance agent."""
    with case.engine.begin() as connection:
        connection.execute(
            schema.employees.insert(),
            {"company_id": "alpha", "id": employee_id, "name": employee_id},
        )
        connection.execute(
            schema.agents.insert(),
            {
                "company_id": "alpha",
                "id": agent_id,
                "employee_id": employee_id,
                "name": agent_id,
            },
        )
        connection.execute(
            schema.delegations.insert(),
            {
                "company_id": "alpha",
                "id": f"{agent_id}-scope",
                "project_id": "finance",
                "employee_id": employee_id,
                "agent_id": agent_id,
                "actions": ["read"],
            },
        )


@pytest.fixture
def source(case: PersistenceCase) -> FakeSource:
    """Register the source with two mapped employees and three documents."""
    _add_actor(case, "employee-auditor", "agent-auditor")
    register_source(case.engine, SETTINGS, ACCOUNTS)
    fake = FakeSource()
    fake.put("travel", TRAVEL)
    fake.put("office", OFFICE, readers=("u-fin", "u-aud"), groups=1)
    fake.put("partner", PARTNER, readers=("u-guest",))
    return fake


def _sync(
    case: PersistenceCase, fake: FakeSource, at: datetime = SYNC_AT
) -> SyncReport:
    """Run one sync against the fake source at a fixed time."""
    return synchronize(
        case.engine,
        lambda settings: fake,
        company_id="alpha",
        source_id="handbook",
        clock=lambda: at,
    )


AUDITOR = {"employee_id": "employee-auditor", "agent_id": "agent-auditor"}
FINANCE = {"employee_id": "employee-finance", "agent_id": "agent-finance"}


def _access(
    document_id: str,
    number: int = 1,
    action: Action = Action.READ,
    *,
    actor: dict[str, str] = FINANCE,
) -> AccessRequest:
    """Build a request for one synced version as an agent."""
    return AccessRequest(
        request_id=f"req-{uuid4()}",
        company_id="alpha",
        project_id="finance",
        employee_id=actor["employee_id"],
        agent_id=actor["agent_id"],
        document_id=document_id,
        version_id=derive_version_id("alpha", document_id, number),
        action=action,
    )


def _read(
    case: PersistenceCase,
    document_id: str,
    number: int = 1,
    *,
    at: datetime = TIMESTAMP,
    actor: dict[str, str] = FINANCE,
) -> ReasonCode:
    """Read one synced version and return the decision reason."""
    result = case.store.read_version(
        _access(document_id, number, actor=actor), occurred_at=at
    )
    return result.decision.reason


def _scope(request_id: str, actor: dict[str, str] = FINANCE) -> ScopeRequest:
    """Build a project-wide read request of an agent."""
    return ScopeRequest(
        request_id=request_id,
        company_id="alpha",
        project_id="finance",
        employee_id=actor["employee_id"],
        agent_id=actor["agent_id"],
        action=Action.READ,
    )


def _search(
    case: PersistenceCase,
    query: str,
    *,
    at: datetime = TIMESTAMP,
    actor: dict[str, str] = FINANCE,
) -> ContextResult:
    """Search the finance project as an agent."""
    return case.store.search_context(
        _scope(f"search-{uuid4()}", actor),
        query=query,
        max_items=10,
        occurred_at=at,
    )


def _found(
    case: PersistenceCase,
    query: str,
    *,
    at: datetime = TIMESTAMP,
    actor: dict[str, str] = FINANCE,
) -> list[tuple[str, int]]:
    """List (document, version number) pairs a search returns."""
    return [
        (item.document_id, item.version_number)
        for item in _search(case, query, at=at, actor=actor).items
    ]


def _rows(case: PersistenceCase, table: Any) -> list[dict[str, Any]]:
    """Read every row of a table for assertions."""
    with case.engine.connect() as connection:
        return [dict(row) for row in connection.execute(select(table)).mappings()]


def _state(case: PersistenceCase) -> str:
    """Serialize memory and source state so unchanged state can be compared."""
    tables = (
        schema.documents,
        schema.versions,
        schema.grants,
        schema.source_documents,
        schema.sources,
    )
    return json.dumps(
        {
            table.name: sorted(
                _rows(case, table), key=lambda row: json.dumps(row, default=str)
            )
            for table in tables
        },
        default=str,
        sort_keys=True,
    )


def test_registration_is_validated_and_all_or_nothing(case: PersistenceCase) -> None:
    """Unknown projects or employees and reused IDs or prefixes store nothing."""
    with pytest.raises(ValueError, match="unknown employees"):
        register_source(case.engine, SETTINGS, {"u-fin": "employee-missing"})
    with pytest.raises(ValueError, match="project"):
        register_source(case.engine, replace(SETTINGS, project_id="missing"), {})
    with pytest.raises(ValueError):
        register_source(case.engine, SETTINGS, {"bad principal": "employee-finance"})
    assert row_count(case.engine, schema.sources) == 0
    register_source(case.engine, SETTINGS, {"u-fin": "employee-finance"})
    for duplicate in (SETTINGS, replace(SETTINGS, id="other")):
        with pytest.raises(ValueError, match="already registered"):
            register_source(case.engine, duplicate, {})
    assert row_count(case.engine, schema.sources) == 1
    assert row_count(case.engine, schema.source_accounts) == 1
    with pytest.raises(LookupError):
        synchronize(
            case.engine,
            lambda settings: FakeSource(),
            company_id="alpha",
            source_id="x",
        )


def test_first_sync_imports_versions_and_read_only_grants(
    case: PersistenceCase, source: FakeSource
) -> None:
    """Mapped readers get read grants; unmapped users and groups get nothing."""
    report = _sync(case, source)
    assert report.outcome is SyncOutcome.SUCCEEDED and report.failure is None
    counts = report.counts
    assert (counts.listed, counts.fetched, counts.created) == (3, 3, 3)
    assert (counts.unmapped_readers, counts.grants_added) == (2, 3)
    documents = {row["id"]: row for row in _rows(case, schema.documents)}
    assert {name for name in documents if name.startswith("hb-")} == {
        "hb-travel",
        "hb-office",
        "hb-partner",
    }
    assert all(documents[name]["active"] for name in ("hb-travel", "hb-partner"))
    versions = {
        row["document_id"]: row
        for row in _rows(case, schema.versions)
        if row["document_id"].startswith("hb-")
    }
    assert versions["hb-travel"]["source"] == "handbook:travel@r1"
    assert versions["hb-travel"]["number"] == 1
    grants = [
        row
        for row in _rows(case, schema.grants)
        if row["document_id"].startswith("hb-")
    ]
    assert sorted((row["document_id"], row["employee_id"]) for row in grants) == [
        ("hb-office", "employee-auditor"),
        ("hb-office", "employee-finance"),
        ("hb-travel", "employee-finance"),
    ]
    assert all(row["actions"] == ["read"] and row["active"] for row in grants)
    (source_row,) = _rows(case, schema.sources)
    assert source_row["confirmed_at"] == SYNC_AT
    assert _read(case, "hb-travel") is ReasonCode.ALLOWED
    assert (
        _read(case, "hb-travel", actor=AUDITOR)
        is ReasonCode.EMPLOYEE_PERMISSION_MISSING
    )
    assert _read(case, "hb-partner") is ReasonCode.EMPLOYEE_PERMISSION_MISSING
    assert _found(case, "badges") == [("hb-office", 1)]
    assert _found(case, "badges", actor=AUDITOR) == [("hb-office", 1)]
    assert _found(case, "train tickets", actor=AUDITOR) == []
    (run,) = _rows(case, schema.source_sync_runs)
    assert (run["outcome"], run["failure"], run["created"]) == ("succeeded", None, 3)


def test_repeated_sync_fetches_no_text_and_changes_nothing(
    case: PersistenceCase, source: FakeSource
) -> None:
    """A retry of the same snapshot only renews the confirmation."""
    _sync(case, source)
    before = _rows(case, schema.versions), _rows(case, schema.grants)
    source.fetched.clear()
    report = _sync(case, source, SYNC_AT + timedelta(minutes=5))
    assert source.fetched == []
    assert report.counts.unchanged == 3
    assert (report.counts.fetched, report.counts.created, report.counts.updated) == (
        0,
        0,
        0,
    )
    assert (report.counts.grants_added, report.counts.grants_revoked) == (0, 0)
    assert (_rows(case, schema.versions), _rows(case, schema.grants)) == before
    (source_row,) = _rows(case, schema.sources)
    assert source_row["confirmed_at"] == SYNC_AT + timedelta(minutes=5)


def test_new_revision_adds_a_version_that_search_uses(
    case: PersistenceCase, source: FakeSource
) -> None:
    """Changed text gets the next version; same text under a new revision does not."""
    _sync(case, source)
    source.put("travel", TRAVEL.replace("two", "three"), revision="r2")
    report = _sync(case, source, SYNC_AT + timedelta(minutes=1))
    assert (report.counts.updated, report.counts.fetched) == (1, 1)
    (item,) = _search(case, "train tickets").items[:1]
    assert (item.document_id, item.version_number) == ("hb-travel", 2)
    assert "three days" in item.text and item.source == "handbook:travel@r2"
    # History stays readable; a citation of version 1 still verifies.
    assert _read(case, "hb-travel", 1) is ReasonCode.ALLOWED
    source.put("travel", TRAVEL.replace("two", "three"), revision="r3")
    again = _sync(case, source, SYNC_AT + timedelta(minutes=2))
    assert (again.counts.updated, again.counts.unchanged, again.counts.fetched) == (
        0,
        3,
        1,
    )
    assert (
        max(
            row["number"]
            for row in _rows(case, schema.versions)
            if row["document_id"] == "hb-travel"
        )
        == 2
    )


def test_revoked_reader_loses_access_when_the_sync_commits(
    case: PersistenceCase, source: FakeSource
) -> None:
    """Removing a reader in the source removes reads, search and the grant."""
    _sync(case, source)
    source.put("office", OFFICE, readers=("u-aud",))
    report = _sync(case, source, SYNC_AT + timedelta(minutes=1))
    assert report.counts.grants_revoked == 1
    assert _read(case, "hb-office") is ReasonCode.EMPLOYEE_PERMISSION_MISSING
    assert _found(case, "badges") == []
    assert _found(case, "badges", actor=AUDITOR) == [("hb-office", 1)]


def test_deleted_document_becomes_unavailable_and_can_return(
    case: PersistenceCase, source: FakeSource
) -> None:
    """Absence from a complete listing deletes; listing it again restores it."""
    _sync(case, source)
    assert _found(case, "train") == [("hb-travel", 1)]
    history = row_count(case.engine, schema.context_items)
    removed = source.documents.pop("travel")
    report = _sync(case, source, SYNC_AT + timedelta(minutes=1))
    assert (report.counts.deleted, report.counts.grants_revoked) == (1, 1)
    assert _read(case, "hb-travel") is ReasonCode.DOCUMENT_UNAVAILABLE
    assert _found(case, "train") == []
    # Earlier history keeps its references; it never held the text.
    assert row_count(case.engine, schema.context_items) == history
    source.documents["travel"] = removed
    source.fetched.clear()
    back = _sync(case, source, SYNC_AT + timedelta(minutes=2))
    assert (back.counts.restored, back.counts.unchanged, back.counts.fetched) == (
        1,
        3,
        0,
    )
    assert _read(case, "hb-travel") is ReasonCode.ALLOWED


def test_rejected_text_withholds_new_and_changed_documents(
    case: PersistenceCase, source: FakeSource
) -> None:
    """Text that fails intake rules makes the document unavailable until fixed."""
    source.put("budget", b"Budget \x07 draft.")
    first = _sync(case, source)
    assert first.withheld == (("budget", "control_characters"),)
    assert first.counts.withheld == 1
    documents = {row["id"]: row for row in _rows(case, schema.documents)}
    assert documents["hb-budget"]["active"] is False
    assert not [
        row for row in _rows(case, schema.versions) if row["document_id"] == "hb-budget"
    ]
    source.put("travel", b"\xff\xfe", revision="r2")
    second = _sync(case, source, SYNC_AT + timedelta(minutes=1))
    assert dict(second.withheld) == {
        "budget": "control_characters",
        "travel": "not_utf8",
    }
    # Fail closed: the earlier valid version is not served for a rejected change.
    assert _read(case, "hb-travel") is ReasonCode.DOCUMENT_UNAVAILABLE
    status = sync_status(
        case.engine, company_id="alpha", source_id="handbook", now=TIMESTAMP
    )
    assert status["documents"] == {"active": 2, "deleted": 0, "withheld": 2}
    assert [entry["external_id"] for entry in status["withheld"]] == [
        "budget",
        "travel",
    ]
    source.put("travel", TRAVEL.replace("two", "four"), revision="r3")
    source.put("budget", "Budget draft.")
    third = _sync(case, source, SYNC_AT + timedelta(minutes=2))
    assert (third.counts.updated, third.counts.created, third.counts.restored) == (
        1,
        1,
        1,
    )
    assert _read(case, "hb-travel", 2) is ReasonCode.ALLOWED
    assert _read(case, "hb-budget") is ReasonCode.ALLOWED


@pytest.mark.parametrize(
    "failure",
    [SyncFailure.UNAVAILABLE, SyncFailure.TIMEOUT, SyncFailure.UNAUTHORIZED],
)
def test_failed_sync_applies_nothing_and_access_ends_with_the_window(
    case: PersistenceCase, source: FakeSource, failure: SyncFailure
) -> None:
    """After a failure only the old confirmation remains, and it expires."""
    _sync(case, source)
    before = _state(case)
    source.documents.pop("travel")
    source.failure = failure
    report = _sync(case, source, SYNC_AT + timedelta(minutes=5))
    assert (report.outcome, report.failure) == (SyncOutcome.FAILED, failure)
    assert _state(case) == before
    runs = sorted(
        _rows(case, schema.source_sync_runs), key=lambda row: row["started_at"]
    )
    assert [row["outcome"] for row in runs] == ["succeeded", "failed"]
    assert runs[1]["failure"] == failure.value and runs[1]["created"] == 0
    almost = EXPIRES - timedelta(microseconds=1)
    assert _read(case, "hb-travel", at=almost) is ReasonCode.ALLOWED
    assert _found(case, "train", at=almost) == [("hb-travel", 1)]
    assert _read(case, "hb-travel", at=EXPIRES) is ReasonCode.SOURCE_UNCONFIRMED
    assert _found(case, "train", at=EXPIRES) == []
    # Documents that did not come from the source are not affected.
    assert _found(case, "revision", at=EXPIRES) == [("finance-policy", 1)]
    status = sync_status(
        case.engine, company_id="alpha", source_id="handbook", now=EXPIRES
    )
    assert status["confirmed"] is False
    assert status["last_run"]["failure"] == failure.value
    source.failure = None
    recovered = _sync(case, source, EXPIRES + timedelta(minutes=1))
    assert recovered.counts.deleted == 1
    assert _read(case, "hb-office", at=EXPIRES + timedelta(minutes=2)) is (
        ReasonCode.ALLOWED
    )


def test_partial_listing_never_deletes(
    case: PersistenceCase, source: FakeSource
) -> None:
    """A malformed later page fails the sync before absence can delete anything."""
    source.put("extra", "Extra text.")
    _sync(case, source)
    before = _state(case)
    source.documents.pop("travel")
    source.broken_page = 1
    report = _sync(case, source, SYNC_AT + timedelta(minutes=1))
    assert report.failure is SyncFailure.INVALID_LISTING
    assert _state(case) == before


def test_answers_are_withheld_when_the_source_ages_or_revokes_mid_run(
    case: PersistenceCase, source: FakeSource
) -> None:
    """The final recheck applies the source gate and a revocation by sync."""
    _sync(case, source)
    question = "When are train tickets booked?"

    def run_until_finish(run_id: str) -> Any:
        """Search and draft an answer at TIMESTAMP, returning the draft run."""
        calls = iter(range(1, 5))

        def search(query: str, max_items: int) -> ContextResult:
            """Search as the finance agent."""
            return case.store.search_context(
                _scope(f"{run_id}-search-{next(calls)}"),
                query=query,
                max_items=max_items,
                occurred_at=TIMESTAMP,
            )

        draft = run_agent(question, provider=OfflineProvider(), search=search)
        assert draft.outcome is RunOutcome.ANSWERED
        return draft

    aged = case.store.finish_run(
        _scope("run-aged"),
        run_until_finish("run-aged"),
        question=question,
        occurred_at=EXPIRES,
    )
    assert (aged.outcome, aged.reason) == (
        RunOutcome.EVIDENCE_REVOKED,
        ReasonCode.SOURCE_UNCONFIRMED,
    )
    assert aged.answer is None and aged.citations == ()
    draft = run_until_finish("run-revoked")
    source.put("travel", TRAVEL, readers=())
    _sync(case, source, TIMESTAMP)
    revoked = case.store.finish_run(
        _scope("run-revoked"), draft, question=question, occurred_at=TIMESTAMP
    )
    assert (revoked.outcome, revoked.reason) == (
        RunOutcome.EVIDENCE_REVOKED,
        ReasonCode.EMPLOYEE_PERMISSION_MISSING,
    )


def test_synced_documents_are_read_only_and_the_sync_owns_their_grants(
    case: PersistenceCase, source: FakeSource
) -> None:
    """A manual write grant cannot open a mirror to writes and is removed."""
    _sync(case, source)
    with case.engine.begin() as connection:
        connection.execute(
            schema.grants.insert(),
            {
                "company_id": "alpha",
                "id": "manual-write",
                "project_id": "finance",
                "employee_id": "employee-finance",
                "document_id": "hb-travel",
                "actions": ["read", "write"],
            },
        )
    request = _access("hb-travel", action=Action.WRITE)
    result = case.store.write_version(
        request,
        new_version=DocumentVersion(
            id=derive_version_id("alpha", "hb-travel", 2),
            company_id="alpha",
            document_id="hb-travel",
            number=2,
            source="agent",
            content="Local edit.",
        ),
        expected_number=1,
        occurred_at=TIMESTAMP,
    )
    assert result.decision.reason is ReasonCode.SOURCE_READ_ONLY
    (audit,) = [
        row for row in _rows(case, schema.audit_events) if row["action"] == "write"
    ]
    assert (audit["allowed"], audit["reason"]) == (False, "source_read_only")
    report = _sync(case, source, SYNC_AT + timedelta(minutes=1))
    assert report.counts.grants_revoked == 1
    manual = [row for row in _rows(case, schema.grants) if row["id"] == "manual-write"]
    assert manual[0]["active"] is False
    assert _read(case, "hb-travel") is ReasonCode.ALLOWED


def test_conflicting_document_id_fails_without_changes(
    case: PersistenceCase, source: FakeSource
) -> None:
    """A source cannot take over a memory document it did not create."""
    ingest_documents(
        case.engine,
        company_id="alpha",
        project_id="finance",
        items=(
            IntakeItem(
                document_id="hb-travel",
                title="Local travel",
                source="file:hb-travel.txt",
                raw=b"Local travel text.",
            ),
        ),
        grants=(
            IntakeGrant(
                employee_id="employee-finance", actions=frozenset({Action.READ})
            ),
        ),
    )
    before = _state(case)
    report = _sync(case, source)
    assert report.failure is SyncFailure.DOCUMENT_CONFLICT
    assert _state(case) == before
    assert _found(case, "local travel") == [("hb-travel", 1)]


def test_superseded_disabled_and_oversized_syncs_apply_nothing(
    case: PersistenceCase, source: FakeSource, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Older snapshots, inactive sources and project limits are refused."""
    _sync(case, source, SYNC_AT)
    listed = (
        ListedDocument(
            external_id="travel",
            title="Title travel",
            revision="r1",
            principals=frozenset(),
        ),
    )
    with pytest.raises(SyncFailed) as raised:
        apply_snapshot(
            case.engine,
            SETTINGS,
            Snapshot(documents=listed, contents={}),
            run_id=str(uuid4()),
            started_at=SYNC_AT - timedelta(seconds=1),
        )
    assert raised.value.failure is SyncFailure.SUPERSEDED
    with pytest.raises(SyncFailed) as raised:
        apply_snapshot(
            case.engine,
            replace(SETTINGS, space="moved"),
            Snapshot(documents=listed, contents={}),
            run_id=str(uuid4()),
            started_at=TIMESTAMP,
        )
    assert raised.value.failure is SyncFailure.SUPERSEDED

    source.put("extra", "Extra text.")
    monkeypatch.setattr(source_store, "MAX_PROJECT_DOCUMENTS", 4)
    limited = _sync(case, source, SYNC_AT + timedelta(minutes=1))
    assert limited.failure is SyncFailure.PROJECT_DOCUMENT_LIMIT

    with case.engine.begin() as connection:
        connection.execute(schema.sources.update().values(active=False))
    disabled = _sync(case, source, SYNC_AT + timedelta(minutes=2))
    assert disabled.failure is SyncFailure.SOURCE_DISABLED
    # An inactive source vouches for nothing, even inside the window.
    assert _read(case, "hb-travel") is ReasonCode.SOURCE_UNCONFIRMED
    assert [row["outcome"] for row in _rows(case, schema.source_sync_runs)].count(
        "failed"
    ) == 2


def test_history_status_and_logs_hold_no_text_or_token(
    case: PersistenceCase, source: FakeSource, caplog: pytest.LogCaptureFixture
) -> None:
    """Runs keep counts only; failures are logged by code."""
    caplog.set_level(logging.DEBUG)
    _sync(case, source)
    source.failure = SyncFailure.UNAVAILABLE
    _sync(case, source, SYNC_AT + timedelta(minutes=1))
    history = json.dumps(
        [_rows(case, table) for table in SOURCE_TABLES], default=str
    ).casefold()
    for phrase in ("train tickets", "badges", "unmapped account"):
        assert phrase not in history
    assert "source sync failed: unavailable" in caplog.text
    assert "badges" not in caplog.text.casefold()
    status = sync_status(
        case.engine, company_id="alpha", source_id="handbook", now=TIMESTAMP
    )
    assert status["confirmed"] is True
    assert status["confirmed_until"] == EXPIRES.isoformat()
    assert status["last_run"]["outcome"] == "failed"
    assert "badges" not in json.dumps(status).casefold()


@pytest.fixture
def lab(case: PersistenceCase) -> Iterator[tuple[LabServer, SourceSettings]]:
    """Serve the first lab fixture on loopback and register it as the source."""
    _add_actor(case, "employee-engineer", "agent-engineer")
    server = LabServer(load_snapshot(ROOT / "fixtures/source/handbook-1.json"), TOKEN)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    settings = replace(
        SETTINGS, base_url=f"http://127.0.0.1:{server.server_address[1]}"
    )
    register_source(
        case.engine,
        settings,
        {"u-fin": "employee-finance", "u-eng": "employee-engineer"},
    )
    try:
        yield server, settings
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_http_lab_source_feeds_cited_answers_and_follows_its_changes(
    case: PersistenceCase, lab: tuple[LabServer, SourceSettings]
) -> None:
    """Load, change, delete, revoke and outage through HTTP on both sides."""
    from app.main import create_app

    server, settings = lab

    def sync(at: datetime) -> SyncReport:
        """Synchronize through the real HTTP connector."""
        return synchronize(
            case.engine,
            lambda value: HandbookClient(value, TOKEN),
            company_id="alpha",
            source_id="handbook",
            clock=lambda: at,
        )

    first = sync(SYNC_AT)
    assert (first.outcome, first.counts.created, first.counts.unmapped_readers) == (
        SyncOutcome.SUCCEEDED,
        4,
        2,
    )
    token = issue_credential(
        case.engine,
        company_id="alpha",
        credential_id="cred-lab",
        agent_id="agent-finance",
        issued_at=TIMESTAMP - timedelta(minutes=5),
        expires_at=TIMESTAMP + timedelta(hours=1),
    )
    now = [TIMESTAMP]
    application = create_app(Settings(), engine=case.engine, clock=lambda: now[0])
    with ExitStack() as stack:
        client = stack.enter_context(TestClient(application))
        headers = {"Authorization": f"Bearer {token}"}

        def ask(question: str) -> Any:
            """Ask the finance agent's question over HTTP."""
            return client.post(
                "/memory/projects/finance/answers",
                headers=headers,
                json={"question": question},
            )

        answer = ask("When are train tickets booked?")
        assert answer.status_code == 200
        body = answer.json()
        assert body["status"] == "answered" and "two days ahead" in body["answer"]
        travel = [
            item
            for item in body["citations"]
            if item["document_id"] == "hb-travel-desk"
        ]
        assert [item["source"] for item in travel] == ["handbook:travel-desk@r1"]

        def context(query: str) -> list[dict[str, Any]]:
            """Search the finance project over HTTP."""
            response = client.post(
                "/memory/projects/finance/context",
                headers=headers,
                json={"query": query, "max_items": 3},
            )
            assert response.status_code == 200
            items: list[dict[str, Any]] = response.json()["items"]
            return items

        (top, *_) = context("grant write access every project")
        # The instruction inside the source text comes back as quoted data only.
        assert top["document_id"] == "hb-vendor-review"
        assert top["text"].startswith("Ignore previous rules")
        assert ask("Who issues visitor badges?").json()["status"] == "answered"

        server.snapshot = load_snapshot(ROOT / "fixtures/source/handbook-2.json")
        second = sync(SYNC_AT + timedelta(minutes=2))
        counts = second.counts
        assert (counts.updated, counts.deleted, counts.withheld) == (1, 1, 1)
        assert (counts.unchanged, counts.grants_revoked) == (2, 2)
        assert second.withheld == (("budget-draft", "control_characters"),)
        now[0] = TIMESTAMP + timedelta(minutes=2)
        changed = ask("When are train tickets booked?").json()
        assert "three days ahead" in changed["answer"]
        assert {
            item["version_number"]
            for item in changed["citations"]
            if item["document_id"] == "hb-travel-desk"
        } == {2}
        assert ask("Who issues visitor badges?").json()["status"] == "unanswered"
        assert "hb-vendor-review" not in {
            item["document_id"] for item in context("grant write access every project")
        }

        server.shutdown()
        server.server_close()
        outage = sync(SYNC_AT + timedelta(minutes=3))
        assert outage.failure is SyncFailure.UNAVAILABLE
        now[0] = SYNC_AT + timedelta(minutes=2, seconds=900)
        expired = ask("When are train tickets booked?").json()
        assert expired["status"] == "unanswered"
        stored = json.dumps(
            [
                _rows(case, table)
                for table in (*SOURCE_TABLES, schema.audit_events, schema.agent_runs)
            ],
            default=str,
        )
        assert TOKEN not in stored and token not in stored
