"""Verify all-or-nothing plain-text intake on actual PostgreSQL."""

import pytest
from sqlalchemy import select

from app.domain.models import Action
from app.domain.versions import derive_version_id
from app.persistence import schema
from app.persistence.intake import (
    MAX_BATCH,
    IntakeGrant,
    IntakeItem,
    IntakeRejected,
    ingest_documents,
)
from tests.persistence.conftest import PersistenceCase, row_count

pytestmark = pytest.mark.postgres

READER = (
    IntakeGrant(employee_id="employee-finance", actions=frozenset({Action.READ})),
)


def _item(name: str, raw: bytes = b"Fictional travel limit.") -> IntakeItem:
    """Describe one fictional file for the finance project."""
    return IntakeItem(document_id=name, title=name, source=f"file:{name}.txt", raw=raw)


def test_valid_batch_creates_documents_versions_and_grants(
    case: PersistenceCase,
) -> None:
    """Every file becomes a document with version 1, its source and the grants."""
    versions = ingest_documents(
        case.engine,
        company_id="alpha",
        project_id="finance",
        items=(_item("travel"), _item("invoices", "Счета\r\nи лимиты".encode())),
        grants=READER,
    )
    assert [version.id for version in versions] == [
        derive_version_id("alpha", "travel", 1),
        derive_version_id("alpha", "invoices", 1),
    ]
    with case.engine.connect() as connection:
        stored = connection.execute(
            select(schema.versions.c.source, schema.versions.c.content).where(
                schema.versions.c.document_id == "invoices"
            )
        ).one()
        grants = connection.execute(
            select(schema.grants.c.document_id, schema.grants.c.actions).where(
                schema.grants.c.document_id.in_(["travel", "invoices"])
            )
        ).all()
    assert stored == ("file:invoices.txt", "Счета\nи лимиты")
    assert sorted(grants) == [("invoices", ["read"]), ("travel", ["read"])]


def test_one_corrupted_file_rejects_the_whole_batch(case: PersistenceCase) -> None:
    """All problems are reported together and nothing is written."""
    with pytest.raises(IntakeRejected) as rejected:
        ingest_documents(
            case.engine,
            company_id="alpha",
            project_id="finance",
            items=(
                _item("good"),
                _item("broken", b"\xff\xfe"),
                _item("empty", b"  "),
                _item("good"),
                _item("bad id!", b"text"),
            ),
            grants=READER,
        )
    assert rejected.value.problems == (
        ("broken", "not_utf8"),
        ("empty", "empty"),
        ("good", "duplicate_document"),
        ("bad id!", "invalid_metadata"),
    )
    assert row_count(case.engine, schema.documents) == 1
    assert row_count(case.engine, schema.versions) == 1


@pytest.mark.parametrize(
    ("kwargs", "problem"),
    [
        ({"project_id": "missing"}, ("missing", "project_unavailable")),
        ({"items": (_item("finance-policy"),)}, ("finance-policy", "document_exists")),
        (
            {
                "grants": (
                    IntakeGrant(employee_id="nobody", actions=frozenset({Action.READ})),
                )
            },
            ("grant:nobody", "employee_unavailable"),
        ),
        ({"items": ()}, ("batch", "batch_size")),
        (
            {"items": tuple(_item(f"doc-{index}") for index in range(MAX_BATCH + 1))},
            ("batch", "batch_size"),
        ),
    ],
)
def test_scope_and_limit_problems_reject_without_writes(
    case: PersistenceCase, kwargs: dict[str, object], problem: tuple[str, str]
) -> None:
    """Unknown projects, existing IDs, unknown employees and batch sizes fail."""
    arguments: dict[str, object] = {
        "company_id": "alpha",
        "project_id": "finance",
        "items": (_item("new-doc"),),
        "grants": READER,
        **kwargs,
    }
    with pytest.raises(IntakeRejected) as rejected:
        ingest_documents(case.engine, **arguments)  # type: ignore[arg-type]
    assert problem in rejected.value.problems
    assert row_count(case.engine, schema.documents) == 1


def test_project_document_limit_is_enforced(
    case: PersistenceCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project cannot grow past its document limit through intake."""
    monkeypatch.setattr("app.persistence.intake.MAX_PROJECT_DOCUMENTS", 2)
    with pytest.raises(IntakeRejected) as rejected:
        ingest_documents(
            case.engine,
            company_id="alpha",
            project_id="finance",
            items=(_item("one"), _item("two")),
        )
    assert rejected.value.problems == (("finance", "project_document_limit"),)
    assert row_count(case.engine, schema.documents) == 1
