"""Evaluate explicit offline snapshots with deny-by-default document access."""

from app.domain.models import (
    AccessDecision,
    AccessRequest,
    Agent,
    Company,
    Delegation,
    Document,
    DocumentVersion,
    Employee,
    Grant,
    Project,
    ReasonCode,
)


def evaluate_access(
    request: AccessRequest,
    *,
    company: Company | None,
    project: Project | None,
    employee: Employee | None,
    agent: Agent | None,
    delegation: Delegation | None,
    document: Document | None,
    version: DocumentVersion | None,
    grants: tuple[Grant, ...] = (),
) -> AccessDecision:
    """Check the supplied snapshot without reading content or proving actor identity."""
    if not isinstance(request, AccessRequest):
        raise TypeError("request must be an AccessRequest")
    for value, expected in (
        (company, Company),
        (project, Project),
        (employee, Employee),
        (agent, Agent),
        (delegation, Delegation),
        (document, Document),
        (version, DocumentVersion),
    ):
        if value is not None and not isinstance(value, expected):
            raise TypeError("snapshot entries must have their declared entity types")
    if not isinstance(grants, tuple) or any(
        not isinstance(grant, Grant) for grant in grants
    ):
        raise TypeError("grants must be a tuple of Grant objects")
    if len({grant.id for grant in grants}) != len(grants):
        raise ValueError("grant identifiers must be unique in the snapshot")

    def deny(reason: ReasonCode) -> AccessDecision:
        """Keep a refusal tied to the evaluated request without document contents."""
        return AccessDecision(request=request, allowed=False, reason=reason)

    if company is None or not company.active:
        return deny(ReasonCode.COMPANY_UNAVAILABLE)
    if company.id != request.company_id:
        return deny(ReasonCode.COMPANY_MISMATCH)

    if project is None or not project.active:
        return deny(ReasonCode.PROJECT_UNAVAILABLE)
    if project.id != request.project_id:
        return deny(ReasonCode.PROJECT_MISMATCH)
    if project.company_id != company.id:
        return deny(ReasonCode.COMPANY_MISMATCH)

    if employee is None or not employee.active or employee.id != request.employee_id:
        return deny(ReasonCode.EMPLOYEE_UNAVAILABLE)
    if employee.company_id != company.id:
        return deny(ReasonCode.COMPANY_MISMATCH)

    if agent is None or not agent.active or agent.id != request.agent_id:
        return deny(ReasonCode.AGENT_UNAVAILABLE)
    if agent.company_id != company.id:
        return deny(ReasonCode.COMPANY_MISMATCH)
    if agent.employee_id != employee.id:
        return deny(ReasonCode.AGENT_EMPLOYEE_MISMATCH)

    if delegation is None or not delegation.active:
        return deny(ReasonCode.DELEGATION_UNAVAILABLE)
    if delegation.company_id != company.id:
        return deny(ReasonCode.COMPANY_MISMATCH)
    if delegation.project_id != project.id:
        return deny(ReasonCode.PROJECT_MISMATCH)
    if delegation.employee_id != employee.id or delegation.agent_id != agent.id:
        return deny(ReasonCode.DELEGATION_MISMATCH)
    if request.action not in delegation.actions:
        return deny(ReasonCode.ACTION_NOT_DELEGATED)

    if document is None or not document.active:
        return deny(ReasonCode.DOCUMENT_UNAVAILABLE)
    if document.id != request.document_id:
        return deny(ReasonCode.DOCUMENT_MISMATCH)
    if document.company_id != company.id:
        return deny(ReasonCode.COMPANY_MISMATCH)
    if document.project_id != project.id:
        return deny(ReasonCode.PROJECT_MISMATCH)

    if version is None or not version.active:
        return deny(ReasonCode.VERSION_UNAVAILABLE)
    if version.id != request.version_id or version.document_id != document.id:
        return deny(ReasonCode.VERSION_MISMATCH)
    if version.company_id != company.id:
        return deny(ReasonCode.COMPANY_MISMATCH)

    has_permission = any(
        grant.active
        and grant.company_id == company.id
        and grant.project_id == project.id
        and grant.employee_id == employee.id
        and grant.document_id == document.id
        and request.action in grant.actions
        for grant in grants
    )
    if not has_permission:
        return deny(ReasonCode.EMPLOYEE_PERMISSION_MISSING)
    return AccessDecision(request=request, allowed=True, reason=ReasonCode.ALLOWED)
