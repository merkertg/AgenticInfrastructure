"""Import validated plain-text documents as trusted setup, all or nothing."""

from dataclasses import dataclass, field
from uuid import uuid4

from sqlalchemy import Engine, func, insert, select

from app.domain.models import Action, Document, DocumentVersion, Grant
from app.domain.text import TextRejected, decode_text
from app.domain.versions import derive_version_id
from app.persistence import schema as db

MAX_BATCH = 50
MAX_PROJECT_DOCUMENTS = 500


@dataclass(frozen=True, slots=True)
class IntakeItem:
    """Name one raw file to import; its bytes are hidden from repr."""

    document_id: str
    title: str
    source: str
    raw: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class IntakeGrant:
    """Give one employee explicit actions on every document of the batch."""

    employee_id: str
    actions: frozenset[Action]


class IntakeRejected(ValueError):
    """List every rejected item and reason so a batch fails predictably."""

    def __init__(self, problems: tuple[tuple[str, str], ...]) -> None:
        """Keep machine-readable problems; messages contain no document text."""
        super().__init__("; ".join(f"{name}: {reason}" for name, reason in problems))
        self.problems = problems


def _prepare(
    company_id: str, project_id: str, items: tuple[IntakeItem, ...]
) -> tuple[list[Document], list[DocumentVersion], list[tuple[str, str]]]:
    """Validate metadata and text of every item before any database write."""
    documents: list[Document] = []
    versions: list[DocumentVersion] = []
    problems: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        name = item.document_id if isinstance(item.document_id, str) else f"#{index}"
        try:
            document = Document(
                id=item.document_id,
                company_id=company_id,
                project_id=project_id,
                title=item.title,
            )
            version = DocumentVersion(
                id=derive_version_id(company_id, item.document_id, 1),
                company_id=company_id,
                document_id=item.document_id,
                number=1,
                source=item.source,
                content=decode_text(item.raw),
            )
        except TextRejected as rejection:
            problems.append((name[:128], rejection.reason.value))
            continue
        except (TypeError, ValueError):
            problems.append((name[:128], "invalid_metadata"))
            continue
        if document.id in seen:
            problems.append((document.id, "duplicate_document"))
            continue
        seen.add(document.id)
        documents.append(document)
        versions.append(version)
    return documents, versions, problems


def ingest_documents(
    engine: Engine,
    *,
    company_id: str,
    project_id: str,
    items: tuple[IntakeItem, ...],
    grants: tuple[IntakeGrant, ...] = (),
) -> tuple[DocumentVersion, ...]:
    """Create documents, their first versions and grants together, or nothing."""
    if not isinstance(items, tuple) or not 1 <= len(items) <= MAX_BATCH:
        raise IntakeRejected((("batch", "batch_size"),))
    documents, versions, problems = _prepare(company_id, project_id, items)
    grant_rows: list[Grant] = []
    for grant in grants:
        for document in documents:
            try:
                grant_rows.append(
                    Grant(
                        id=f"grant-{uuid4()}",
                        company_id=company_id,
                        project_id=project_id,
                        employee_id=grant.employee_id,
                        document_id=document.id,
                        actions=grant.actions,
                    )
                )
            except (TypeError, ValueError):
                problems.append((f"grant:{grant.employee_id}"[:128], "invalid_grant"))
                break
    if problems:
        raise IntakeRejected(tuple(problems))
    with engine.begin() as connection:
        active_company = connection.scalar(
            select(db.companies.c.active)
            .where(db.companies.c.id == company_id)
            .with_for_update()
        )
        active_project = connection.scalar(
            select(db.projects.c.active).where(
                db.projects.c.company_id == company_id,
                db.projects.c.id == project_id,
            )
        )
        if not active_company or not active_project:
            raise IntakeRejected(((project_id, "project_unavailable"),))
        existing = connection.scalars(
            select(db.documents.c.id).where(
                db.documents.c.company_id == company_id,
                db.documents.c.id.in_([document.id for document in documents]),
            )
        ).all()
        if existing:
            raise IntakeRejected(
                tuple((name, "document_exists") for name in sorted(existing))
            )
        count = connection.scalar(
            select(func.count())
            .select_from(db.documents)
            .where(
                db.documents.c.company_id == company_id,
                db.documents.c.project_id == project_id,
            )
        )
        if int(count or 0) + len(documents) > MAX_PROJECT_DOCUMENTS:
            raise IntakeRejected(((project_id, "project_document_limit"),))
        employees = set(
            connection.scalars(
                select(db.employees.c.id).where(
                    db.employees.c.company_id == company_id,
                    db.employees.c.id.in_([grant.employee_id for grant in grants]),
                )
            ).all()
        )
        missing = sorted({grant.employee_id for grant in grants} - employees)
        if missing:
            raise IntakeRejected(
                tuple((f"grant:{name}", "employee_unavailable") for name in missing)
            )
        connection.execute(
            insert(db.documents),
            [
                {
                    "company_id": document.company_id,
                    "id": document.id,
                    "project_id": document.project_id,
                    "title": document.title,
                }
                for document in documents
            ],
        )
        connection.execute(
            insert(db.versions),
            [
                {
                    "company_id": version.company_id,
                    "id": version.id,
                    "document_id": version.document_id,
                    "number": version.number,
                    "source": version.source,
                    "content": version.content,
                }
                for version in versions
            ],
        )
        if grant_rows:
            connection.execute(
                insert(db.grants),
                [
                    {
                        "company_id": grant.company_id,
                        "id": grant.id,
                        "project_id": grant.project_id,
                        "employee_id": grant.employee_id,
                        "document_id": grant.document_id,
                        "actions": sorted(action.value for action in grant.actions),
                    }
                    for grant in grant_rows
                ],
            )
    return tuple(versions)
