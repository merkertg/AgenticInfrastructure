"""Demonstrate offline policy decisions using only fictional in-memory values."""

import json
from dataclasses import replace
from datetime import UTC, datetime

from app.domain.audit import make_audit_event
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


def demo_results() -> tuple[tuple[str, AccessDecision], ...]:
    """Build independent fictional scenarios with no configuration or external IO."""
    company = Company(id="alpha", name="Alpha")
    finance = Project(id="finance", company_id=company.id, name="Finance")
    employee = Employee(id="fin-employee", company_id=company.id, name="Finance user")
    agent = Agent(
        id="fin-agent",
        company_id=company.id,
        employee_id=employee.id,
        name="Finance assistant",
    )
    delegation = Delegation(
        id="fin-delegation",
        company_id=company.id,
        project_id=finance.id,
        employee_id=employee.id,
        agent_id=agent.id,
        actions=frozenset({Action.READ, Action.WRITE}),
    )
    document = Document(
        id="fin-policy",
        company_id=company.id,
        project_id=finance.id,
        title="Fictional finance policy",
    )
    version = DocumentVersion(
        id="fin-policy-v1",
        company_id=company.id,
        document_id=document.id,
        number=1,
        source="fixture:finance-policy",
        content="Fictional finance text.",
    )
    grants = (
        Grant(
            id="fin-read",
            company_id=company.id,
            project_id=finance.id,
            employee_id=employee.id,
            document_id=document.id,
            actions=frozenset({Action.READ}),
        ),
    )
    request = AccessRequest(
        request_id="demo-finance",
        company_id=company.id,
        project_id=finance.id,
        employee_id=employee.id,
        agent_id=agent.id,
        document_id=document.id,
        version_id=version.id,
        action=Action.READ,
    )

    def decide(
        name: str,
        *,
        attempt: AccessRequest = request,
        scoped_project: Project = finance,
        actor: Employee = employee,
        registered_agent: Agent = agent,
        scope: Delegation = delegation,
        target: Document = document,
        revision: DocumentVersion = version,
        permissions: tuple[Grant, ...] = grants,
    ) -> tuple[str, AccessDecision]:
        """Evaluate a snapshot with a distinct request ID for its audit value."""
        return name, evaluate_access(
            replace(attempt, request_id=f"demo-{name}"),
            company=company,
            project=scoped_project,
            employee=actor,
            agent=registered_agent,
            delegation=scope,
            document=target,
            version=revision,
            grants=permissions,
        )

    engineering = Project(id="engineering", company_id=company.id, name="Engineering")
    engineer = Employee(id="eng-employee", company_id=company.id, name="Engineer")
    eng_agent = Agent(
        id="eng-agent",
        company_id=company.id,
        employee_id=engineer.id,
        name="Engineering assistant",
    )
    eng_scope = Delegation(
        id="eng-delegation",
        company_id=company.id,
        project_id=engineering.id,
        employee_id=engineer.id,
        agent_id=eng_agent.id,
        actions=frozenset({Action.READ}),
    )
    eng_request = replace(
        request,
        project_id=engineering.id,
        employee_id=engineer.id,
        agent_id=eng_agent.id,
    )
    return (
        decide("finance_read"),
        decide(
            "engineering_finance_denied",
            attempt=eng_request,
            scoped_project=engineering,
            actor=engineer,
            registered_agent=eng_agent,
            scope=eng_scope,
        ),
        decide("foreign_company_denied", target=replace(document, company_id="beta")),
        decide("missing_grant_denied", permissions=()),
        decide("revoked_delegation_denied", scope=replace(delegation, active=False)),
        decide("write_denied", attempt=replace(request, action=Action.WRITE)),
        decide(
            "version_mismatch_denied", revision=replace(version, document_id="other")
        ),
        decide("action_not_delegated", scope=replace(delegation, actions=frozenset())),
    )


def main() -> int:
    """Print reproducible decision and audit metadata without document bodies."""
    occurred_at = datetime(2026, 10, 24, 0, 0, tzinfo=UTC)
    output = []
    for name, decision in demo_results():
        event = make_audit_event(decision, occurred_at=occurred_at)
        output.append(
            {
                "case": name,
                "request_id": event.request.request_id,
                "allowed": event.allowed,
                "reason": event.reason.value,
                "action": event.request.action.value,
                "occurred_at": event.occurred_at.isoformat(),
            }
        )
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
