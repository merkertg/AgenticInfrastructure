"""Build a fictional finance case with explicit employee and agent permissions."""

from dataclasses import dataclass

from app.domain.models import (
    AccessDecision,
    AccessRequest,
    Action,
    Agent,
    Company,
    Delegation,
    Document,
    DocumentVersion,
    Employee,
    Grant,
    Project,
)
from app.domain.policy import evaluate_access


@dataclass(frozen=True)
class AccessCase:
    """Keep the complete explicit policy input convenient to vary in tests."""

    request: AccessRequest
    company: Company | None
    project: Project | None
    employee: Employee | None
    agent: Agent | None
    delegation: Delegation | None
    document: Document | None
    version: DocumentVersion | None
    grants: tuple[Grant, ...]


def finance_case() -> AccessCase:
    """Return an allowed read using only invented company and document data."""
    return AccessCase(
        request=AccessRequest(
            request_id="request-1",
            company_id="alpha",
            project_id="finance",
            employee_id="employee-finance",
            agent_id="agent-finance",
            document_id="finance-policy",
            version_id="finance-policy-v1",
            action=Action.READ,
        ),
        company=Company(id="alpha", name="Alpha"),
        project=Project(id="finance", company_id="alpha", name="Finance"),
        employee=Employee(
            id="employee-finance", company_id="alpha", name="Fictional analyst"
        ),
        agent=Agent(
            id="agent-finance",
            company_id="alpha",
            employee_id="employee-finance",
            name="Finance assistant",
        ),
        delegation=Delegation(
            id="delegation-1",
            company_id="alpha",
            project_id="finance",
            employee_id="employee-finance",
            agent_id="agent-finance",
            actions=frozenset({Action.READ}),
        ),
        document=Document(
            id="finance-policy",
            company_id="alpha",
            project_id="finance",
            title="Fictional finance policy",
        ),
        version=DocumentVersion(
            id="finance-policy-v1",
            company_id="alpha",
            document_id="finance-policy",
            number=1,
            source="fixture:finance-policy",
            content="Fictional internal finance policy.\nRevision one.",
        ),
        grants=(
            Grant(
                id="grant-1",
                company_id="alpha",
                project_id="finance",
                employee_id="employee-finance",
                document_id="finance-policy",
                actions=frozenset({Action.READ}),
            ),
        ),
    )


def evaluate_case(case: AccessCase) -> AccessDecision:
    """Evaluate the supplied snapshot without deriving its expected outcome."""
    return evaluate_access(
        case.request,
        company=case.company,
        project=case.project,
        employee=case.employee,
        agent=case.agent,
        delegation=case.delegation,
        document=case.document,
        version=case.version,
        grants=case.grants,
    )
