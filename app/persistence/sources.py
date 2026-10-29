"""Register one read-only source, apply complete snapshots and gate synced documents.

A sync reads the source outside any transaction, then applies the complete
snapshot in one transaction under the company lock. Synced documents are
readable only while the last applied snapshot is recent enough, and memory
operations never write to them.
"""

import logging
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import asdict, fields
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    ColumnElement,
    Connection,
    Engine,
    Interval,
    and_,
    exists,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.engine import RowMapping

from app.application.source_sync import (
    SYNC_SECONDS,
    Snapshot,
    SourceClient,
    SyncCounts,
    SyncFailed,
    SyncFailure,
    SyncOutcome,
    SyncReport,
    collect_snapshot,
)
from app.domain.models import Action, ReasonCode
from app.domain.source import (
    SOURCE_KIND,
    ChangeKind,
    DocumentChange,
    DocumentState,
    SourceSettings,
    StoredDocument,
    check_principal,
    needs_content,
    plan_sync,
    source_confirmed,
    source_grant_id,
    version_label,
)
from app.domain.versions import derive_version_id
from app.persistence import schema as db
from app.persistence.intake import MAX_PROJECT_DOCUMENTS

logger = logging.getLogger(__name__)
MAX_ACCOUNTS = 1_000
_READ_ONLY = [Action.READ.value]


def _now() -> datetime:
    """Read the current UTC time for sync runs."""
    return datetime.now(UTC)


def _settings(row: RowMapping) -> SourceSettings:
    """Rebuild validated settings from a stored source row."""
    return SourceSettings(
        id=row["id"],
        company_id=row["company_id"],
        project_id=row["project_id"],
        base_url=row["base_url"],
        space=row["space"],
        document_prefix=row["document_prefix"],
        max_age_seconds=row["max_age_seconds"],
    )


def _source_row(
    connection: Connection, company_id: str, source_id: str, *, lock: bool = False
) -> RowMapping | None:
    """Read one source row, optionally locking it for the rest of the transaction."""
    query = select(db.sources).where(
        db.sources.c.company_id == company_id, db.sources.c.id == source_id
    )
    if lock:
        query = query.with_for_update()
    return connection.execute(query).mappings().one_or_none()


def register_source(
    engine: Engine, settings: SourceSettings, accounts: Mapping[str, str]
) -> None:
    """Store a source and its account mapping as trusted setup, all or nothing."""
    if not isinstance(settings, SourceSettings):
        raise TypeError("settings must be SourceSettings")
    if not isinstance(accounts, Mapping) or len(accounts) > MAX_ACCOUNTS:
        raise ValueError(f"accounts must map at most {MAX_ACCOUNTS} principals")
    for principal, employee_id in accounts.items():
        check_principal(principal)
        if not isinstance(employee_id, str):
            raise TypeError("employee IDs must be strings")
    with engine.begin() as connection:
        company_active = connection.scalar(
            select(db.companies.c.active)
            .where(db.companies.c.id == settings.company_id)
            .with_for_update()
        )
        project_active = connection.scalar(
            select(db.projects.c.active).where(
                db.projects.c.company_id == settings.company_id,
                db.projects.c.id == settings.project_id,
            )
        )
        if not company_active or not project_active:
            raise ValueError("the target project is unavailable")
        taken = connection.scalar(
            select(func.count())
            .select_from(db.sources)
            .where(
                db.sources.c.company_id == settings.company_id,
                or_(
                    db.sources.c.id == settings.id,
                    db.sources.c.document_prefix == settings.document_prefix,
                ),
            )
        )
        if taken:
            raise ValueError("the source ID or document prefix is already registered")
        known = set(
            connection.scalars(
                select(db.employees.c.id).where(
                    db.employees.c.company_id == settings.company_id,
                    db.employees.c.id.in_(list(accounts.values())),
                )
            )
        )
        missing = sorted(set(accounts.values()) - known)
        if missing:
            raise ValueError(f"unknown employees: {', '.join(missing)}")
        connection.execute(
            insert(db.sources).values(
                company_id=settings.company_id,
                id=settings.id,
                project_id=settings.project_id,
                kind=SOURCE_KIND,
                base_url=settings.base_url,
                space=settings.space,
                document_prefix=settings.document_prefix,
                max_age_seconds=settings.max_age_seconds,
            )
        )
        if accounts:
            connection.execute(
                insert(db.source_accounts),
                [
                    {
                        "company_id": settings.company_id,
                        "source_id": settings.id,
                        "principal": principal,
                        "employee_id": employee_id,
                    }
                    for principal, employee_id in sorted(accounts.items())
                ],
            )


def _stored(
    connection: Connection, settings: SourceSettings
) -> dict[str, StoredDocument]:
    """Read what memory holds for each document this source ever listed."""
    rows = connection.execute(
        select(db.source_documents, db.documents.c.title)
        .join(
            db.documents,
            and_(
                db.documents.c.company_id == db.source_documents.c.company_id,
                db.documents.c.id == db.source_documents.c.document_id,
            ),
        )
        .where(
            db.source_documents.c.company_id == settings.company_id,
            db.source_documents.c.source_id == settings.id,
        )
    ).mappings()
    return {
        row["external_id"]: StoredDocument(
            external_id=row["external_id"],
            document_id=row["document_id"],
            title=row["title"],
            revision=row["revision"],
            content_sha256=row["content_sha256"],
            state=DocumentState(row["state"]),
        )
        for row in rows
    }


def load_source(
    engine: Engine, company_id: str, source_id: str
) -> tuple[SourceSettings, dict[str, StoredDocument]]:
    """Read settings and stored state to decide which texts must be fetched."""
    with engine.connect() as connection:
        row = _source_row(connection, company_id, source_id)
        if row is None:
            raise LookupError("source is not registered")
        settings = _settings(row)
        if not row["active"]:
            raise SyncFailed(SyncFailure.SOURCE_DISABLED)
        return settings, _stored(connection, settings)


def _apply_change(
    connection: Connection,
    settings: SourceSettings,
    previous: StoredDocument | None,
    change: DocumentChange,
) -> None:
    """Write one document change: its row, an optional new version and its state."""
    company_id = settings.company_id
    if previous is None:
        connection.execute(
            insert(db.documents).values(
                company_id=company_id,
                id=change.document_id,
                project_id=settings.project_id,
                title=change.title,
                active=change.available,
            )
        )
    else:
        connection.execute(
            update(db.documents)
            .where(
                db.documents.c.company_id == company_id,
                db.documents.c.id == change.document_id,
            )
            .values(title=change.title, active=change.available)
        )
    if change.content is not None:
        assert change.revision is not None
        current = connection.scalar(
            select(func.max(db.versions.c.number)).where(
                db.versions.c.company_id == company_id,
                db.versions.c.document_id == change.document_id,
            )
        )
        number = int(current or 0) + 1
        connection.execute(
            insert(db.versions).values(
                company_id=company_id,
                id=derive_version_id(company_id, change.document_id, number),
                document_id=change.document_id,
                number=number,
                source=version_label(settings.id, change.external_id, change.revision),
                content=change.content,
            )
        )
    if change.available:
        state = DocumentState.ACTIVE
    elif change.kind is ChangeKind.DELETED:
        state = DocumentState.DELETED
    else:
        state = DocumentState.WITHHELD
    values = {
        "revision": change.revision,
        "content_sha256": change.content_sha256,
        "state": state.value,
        "withheld_reason": change.withheld_reason,
    }
    if previous is None:
        connection.execute(
            insert(db.source_documents).values(
                company_id=company_id,
                source_id=settings.id,
                external_id=change.external_id,
                project_id=settings.project_id,
                document_id=change.document_id,
                **values,
            )
        )
    else:
        connection.execute(
            update(db.source_documents)
            .where(
                db.source_documents.c.company_id == company_id,
                db.source_documents.c.source_id == settings.id,
                db.source_documents.c.external_id == change.external_id,
            )
            .values(**values)
        )


def _reconcile_grants(
    connection: Connection,
    settings: SourceSettings,
    document_ids: set[str],
    changes: tuple[DocumentChange, ...],
) -> tuple[int, int]:
    """Make each synced document's active grants equal its mapped source readers.

    The source is the only authority for its documents, so any other grant on
    them, including a manual one, is deactivated. Owned grants carry read only.
    """
    company_id = settings.company_id
    desired = {
        (change.document_id, employee_id)
        for change in changes
        if change.available
        for employee_id in change.readers
    }
    rows = connection.execute(
        select(db.grants).where(
            db.grants.c.company_id == company_id,
            db.grants.c.document_id.in_(sorted(document_ids)),
        )
    ).mappings()
    present: set[tuple[str, str]] = set()
    restore: list[str] = []
    revoke: list[str] = []
    added = 0
    for row in rows:
        key = (row["document_id"], row["employee_id"])
        if key in desired and row["id"] == source_grant_id(company_id, *key):
            present.add(key)
            if not row["active"] or row["actions"] != _READ_ONLY:
                restore.append(row["id"])
                added += 0 if row["active"] else 1
        elif row["active"]:
            revoke.append(row["id"])
    for ids, values in (
        (restore, {"active": True, "actions": _READ_ONLY}),
        (revoke, {"active": False}),
    ):
        if ids:
            connection.execute(
                update(db.grants)
                .where(db.grants.c.company_id == company_id, db.grants.c.id.in_(ids))
                .values(**values)
            )
    missing = sorted(desired - present)
    if missing:
        connection.execute(
            insert(db.grants),
            [
                {
                    "company_id": company_id,
                    "id": source_grant_id(company_id, document_id, employee_id),
                    "project_id": settings.project_id,
                    "employee_id": employee_id,
                    "document_id": document_id,
                    "actions": _READ_ONLY,
                }
                for document_id, employee_id in missing
            ],
        )
    return added + len(missing), len(revoke)


def _record(
    connection: Connection,
    company_id: str,
    report: SyncReport,
    started_at: datetime,
    finished_at: datetime,
) -> None:
    """Store run metadata and counts; no names, text, URLs or credentials."""
    connection.execute(
        insert(db.source_sync_runs).values(
            run_id=report.run_id,
            company_id=company_id,
            source_id=report.source_id,
            started_at=started_at,
            finished_at=max(finished_at, started_at),
            outcome=report.outcome.value,
            failure=report.failure.value if report.failure is not None else None,
            **asdict(report.counts),
        )
    )


def apply_snapshot(
    engine: Engine,
    settings: SourceSettings,
    snapshot: Snapshot,
    *,
    run_id: str,
    started_at: datetime,
    clock: Callable[[], datetime] = _now,
) -> SyncReport:
    """Apply a complete snapshot and its run record in one transaction.

    The company lock serializes the change with memory operations. A snapshot
    that started before the last applied one, or that misses a text the current
    state needs, is refused rather than allowed to roll access back.
    """
    with engine.begin() as connection:
        company_active = connection.scalar(
            select(db.companies.c.active)
            .where(db.companies.c.id == settings.company_id)
            .with_for_update()
        )
        row = _source_row(connection, settings.company_id, settings.id, lock=True)
        project_active = connection.scalar(
            select(db.projects.c.active).where(
                db.projects.c.company_id == settings.company_id,
                db.projects.c.id == settings.project_id,
            )
        )
        if row is None or not row["active"] or not company_active or not project_active:
            raise SyncFailed(SyncFailure.SOURCE_DISABLED)
        if _settings(row) != settings or (
            row["confirmed_at"] is not None and row["confirmed_at"] >= started_at
        ):
            raise SyncFailed(SyncFailure.SUPERSEDED)
        stored = _stored(connection, settings)
        accounts = {
            principal: employee_id
            for principal, employee_id in connection.execute(
                select(
                    db.source_accounts.c.principal, db.source_accounts.c.employee_id
                ).where(
                    db.source_accounts.c.company_id == settings.company_id,
                    db.source_accounts.c.source_id == settings.id,
                    db.source_accounts.c.active.is_(True),
                )
            )
        }
        try:
            plan = plan_sync(
                prefix=settings.document_prefix,
                stored=stored,
                listed=snapshot.documents,
                contents=snapshot.contents,
                accounts=accounts,
            )
        except ValueError:
            # Another sync changed the stored state after the texts were fetched.
            raise SyncFailed(SyncFailure.SUPERSEDED) from None
        new_ids = [
            change.document_id
            for change in plan.changes
            if change.external_id not in stored
        ]
        if new_ids:
            if connection.scalar(
                select(func.count())
                .select_from(db.documents)
                .where(
                    db.documents.c.company_id == settings.company_id,
                    db.documents.c.id.in_(new_ids),
                )
            ):
                raise SyncFailed(SyncFailure.DOCUMENT_CONFLICT)
            existing = connection.scalar(
                select(func.count())
                .select_from(db.documents)
                .where(
                    db.documents.c.company_id == settings.company_id,
                    db.documents.c.project_id == settings.project_id,
                )
            )
            if int(existing or 0) + len(new_ids) > MAX_PROJECT_DOCUMENTS:
                raise SyncFailed(SyncFailure.PROJECT_DOCUMENT_LIMIT)
        for change in plan.changes:
            _apply_change(connection, settings, stored.get(change.external_id), change)
        added, revoked = _reconcile_grants(
            connection,
            settings,
            {entry.document_id for entry in stored.values()}
            | {change.document_id for change in plan.changes},
            plan.changes,
        )
        connection.execute(
            update(db.sources)
            .where(
                db.sources.c.company_id == settings.company_id,
                db.sources.c.id == settings.id,
            )
            .values(confirmed_at=started_at)
        )
        report = SyncReport(
            run_id=run_id,
            source_id=settings.id,
            outcome=SyncOutcome.SUCCEEDED,
            counts=SyncCounts(
                listed=len(snapshot.documents),
                fetched=len(snapshot.contents),
                created=plan.count(ChangeKind.CREATED),
                updated=plan.count(ChangeKind.UPDATED),
                unchanged=plan.count(ChangeKind.UNCHANGED),
                restored=sum(1 for change in plan.changes if change.restored),
                deleted=plan.count(ChangeKind.DELETED),
                withheld=plan.count(ChangeKind.WITHHELD),
                unmapped_readers=plan.unmapped_readers,
                grants_added=added,
                grants_revoked=revoked,
            ),
            withheld=tuple(
                (change.external_id, change.withheld_reason)
                for change in plan.changes
                if change.withheld_reason is not None
            ),
        )
        _record(connection, settings.company_id, report, started_at, clock())
    return report


def record_failure(
    engine: Engine,
    *,
    company_id: str,
    source_id: str,
    run_id: str,
    failure: SyncFailure,
    started_at: datetime,
    finished_at: datetime,
) -> SyncReport:
    """Record a run that applied nothing; earlier confirmation keeps aging."""
    report = SyncReport(
        run_id=run_id, source_id=source_id, outcome=SyncOutcome.FAILED, failure=failure
    )
    with engine.begin() as connection:
        _record(connection, company_id, report, started_at, finished_at)
    return report


def synchronize(
    engine: Engine,
    connect: Callable[[SourceSettings], SourceClient],
    *,
    company_id: str,
    source_id: str,
    clock: Callable[[], datetime] = _now,
    monotonic: Callable[[], float] = time.monotonic,
) -> SyncReport:
    """Fetch a complete snapshot outside any transaction, then apply it at once.

    After any failure nothing is applied and the run is still recorded; the
    source's documents stay readable only until the previous confirmation ages.
    """
    run_id = str(uuid4())
    started_at = clock()
    try:
        settings, stored = load_source(engine, company_id, source_id)
        snapshot = collect_snapshot(
            connect(settings),
            lambda entry: needs_content(stored.get(entry.external_id), entry),
            deadline=monotonic() + SYNC_SECONDS,
            clock=monotonic,
        )
        return apply_snapshot(
            engine,
            settings,
            snapshot,
            run_id=run_id,
            started_at=started_at,
            clock=clock,
        )
    except SyncFailed as failed:
        logger.warning("source sync failed: %s", failed.failure.value)
        return record_failure(
            engine,
            company_id=company_id,
            source_id=source_id,
            run_id=run_id,
            failure=failed.failure,
            started_at=started_at,
            finished_at=clock(),
        )


def source_denials(
    connection: Connection,
    company_id: str,
    document_ids: Collection[str],
    action: Action,
    occurred_at: datetime,
) -> dict[str, ReasonCode]:
    """Deny writes to synced documents and reads whose source lacks confirmation."""
    if not document_ids:
        return {}
    rows = connection.execute(
        select(
            db.source_documents.c.document_id,
            db.sources.c.active,
            db.sources.c.confirmed_at,
            db.sources.c.max_age_seconds,
        )
        .join(
            db.sources,
            and_(
                db.sources.c.company_id == db.source_documents.c.company_id,
                db.sources.c.id == db.source_documents.c.source_id,
            ),
        )
        .where(
            db.source_documents.c.company_id == company_id,
            db.source_documents.c.document_id.in_(sorted(document_ids)),
        )
    ).mappings()
    denials: dict[str, ReasonCode] = {}
    for row in rows:
        if action is Action.WRITE:
            denials[row["document_id"]] = ReasonCode.SOURCE_READ_ONLY
        elif not row["active"] or not source_confirmed(
            row["confirmed_at"], row["max_age_seconds"], occurred_at
        ):
            denials[row["document_id"]] = ReasonCode.SOURCE_UNCONFIRMED
    return denials


def lacks_confirmation(occurred_at: datetime) -> ColumnElement[bool]:
    """Match a document row whose source cannot vouch for it at occurred_at.

    This only narrows search candidates in SQL; source_denials() decides.
    """
    expires = db.sources.c.confirmed_at + func.make_interval(
        0, 0, 0, 0, 0, 0, db.sources.c.max_age_seconds, type_=Interval
    )
    return exists().where(
        db.source_documents.c.company_id == db.documents.c.company_id,
        db.source_documents.c.document_id == db.documents.c.id,
        db.sources.c.company_id == db.source_documents.c.company_id,
        db.sources.c.id == db.source_documents.c.source_id,
        or_(
            db.sources.c.active.is_(False),
            db.sources.c.confirmed_at.is_(None),
            expires <= occurred_at,
        ),
    )


def _iso(value: datetime | None) -> str | None:
    """Format a stored time in UTC for status output."""
    return value.astimezone(UTC).isoformat() if value is not None else None


def sync_status(
    engine: Engine, *, company_id: str, source_id: str, now: datetime
) -> dict[str, Any]:
    """Describe confirmation, document states and the last run for operators."""
    with engine.connect() as connection:
        row = _source_row(connection, company_id, source_id)
        if row is None:
            raise LookupError("source is not registered")
        states: dict[str, int] = {
            state: count
            for state, count in connection.execute(
                select(db.source_documents.c.state, func.count())
                .where(
                    db.source_documents.c.company_id == company_id,
                    db.source_documents.c.source_id == source_id,
                )
                .group_by(db.source_documents.c.state)
            )
        }
        withheld = connection.execute(
            select(
                db.source_documents.c.external_id, db.source_documents.c.withheld_reason
            )
            .where(
                db.source_documents.c.company_id == company_id,
                db.source_documents.c.source_id == source_id,
                db.source_documents.c.state == DocumentState.WITHHELD.value,
            )
            .order_by(db.source_documents.c.external_id)
            .limit(50)
        )
        last = (
            connection.execute(
                select(db.source_sync_runs)
                .where(
                    db.source_sync_runs.c.company_id == company_id,
                    db.source_sync_runs.c.source_id == source_id,
                )
                .order_by(db.source_sync_runs.c.started_at.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        withheld_list = [
            {"external_id": external_id, "reason": reason}
            for external_id, reason in withheld
        ]
    confirmed_at = row["confirmed_at"]
    until = (
        confirmed_at + timedelta(seconds=row["max_age_seconds"])
        if confirmed_at is not None
        else None
    )
    return {
        "source_id": source_id,
        "project_id": row["project_id"],
        "active": row["active"],
        "confirmed": bool(row["active"])
        and source_confirmed(confirmed_at, row["max_age_seconds"], now),
        "confirmed_at": _iso(confirmed_at),
        "confirmed_until": _iso(until),
        "documents": {
            state.value: int(states.get(state.value, 0)) for state in DocumentState
        },
        "withheld": withheld_list,
        "last_run": None
        if last is None
        else {
            "run_id": last["run_id"],
            "outcome": last["outcome"],
            "failure": last["failure"],
            "started_at": _iso(last["started_at"]),
            "finished_at": _iso(last["finished_at"]),
            **{entry.name: last[entry.name] for entry in fields(SyncCounts)},
        },
    }
