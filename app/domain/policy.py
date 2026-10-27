"""Evaluate explicit offline snapshots with deny-by-default document access."""

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
    ReasonCode,
    ScopeRequest,
)


def _scope_denial(
    *,
    company_id: str,
    project_id: str,
    employee_id: str,
    agent_id: str,
    action: Action,
    company: Company | None,
    project: Project | None,
    employee: Employee | None,
    agent: Agent | None,
    delegation: Delegation | None,
) -> ReasonCode | None:
    """Return the first company, actor or delegation failure, or None if in scope."""
    if company is None or not company.active:
        return ReasonCode.COMPANY_UNAVAILABLE
    if company.id != company_id:
        return ReasonCode.COMPANY_MISMATCH

    if project is None or not project.active:
        return ReasonCode.PROJECT_UNAVAILABLE
    if project.id != project_id:
        return ReasonCode.PROJECT_MISMATCH
    if project.company_id != company.id:
        return ReasonCode.COMPANY_MISMATCH

    if employee is None or not employee.active or employee.id != employee_id:
        return ReasonCode.EMPLOYEE_UNAVAILABLE
    if employee.company_id != company.id:
        return ReasonCode.COMPANY_MISMATCH

    if agent is None or not agent.active or agent.id != agent_id:
        return ReasonCode.AGENT_UNAVAILABLE
    if agent.company_id != company.id:
        return ReasonCode.COMPANY_MISMATCH
    if agent.employee_id != employee.id:
        return ReasonCode.AGENT_EMPLOYEE_MISMATCH

    if delegation is None or not delegation.active:
        return ReasonCode.DELEGATION_UNAVAILABLE
    if delegation.company_id != company.id:
        return ReasonCode.COMPANY_MISMATCH
    if delegation.project_id != project.id:
        return ReasonCode.PROJECT_MISMATCH
    if delegation.employee_id != employee.id or delegation.agent_id != agent.id:
        return ReasonCode.DELEGATION_MISMATCH
    if action not in delegation.actions:
        return ReasonCode.ACTION_NOT_DELEGATED
    return None


def _check_snapshot_types(
    pairs: tuple[tuple[object, type], ...],
) -> None:
    """Reject snapshot entries whose runtime type differs from the declared entity."""
    for value, expected in pairs:
        if value is not None and not isinstance(value, expected):
            raise TypeError("snapshot entries must have their declared entity types")


def evaluate_scope(
    request: ScopeRequest,
    *,
    company: Company | None,
    project: Project | None,
    employee: Employee | None,
    agent: Agent | None,
    delegation: Delegation | None,
) -> ReasonCode:
    """Check project-wide access before any document of the project is considered."""
    if not isinstance(request, ScopeRequest):
        raise TypeError("request must be a ScopeRequest")
    _check_snapshot_types(
        (
            (company, Company),
            (project, Project),
            (employee, Employee),
            (agent, Agent),
            (delegation, Delegation),
        )
    )
    denial = _scope_denial(
        company_id=request.company_id,
        project_id=request.project_id,
        employee_id=request.employee_id,
        agent_id=request.agent_id,
        action=request.action,
        company=company,
        project=project,
        employee=employee,
        agent=agent,
        delegation=delegation,
    )
    return ReasonCode.ALLOWED if denial is None else denial


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
    _check_snapshot_types(
        (
            (company, Company),
            (project, Project),
            (employee, Employee),
            (agent, Agent),
            (delegation, Delegation),
            (document, Document),
            (version, DocumentVersion),
        )
    )
    if not isinstance(grants, tuple) or any(
        not isinstance(grant, Grant) for grant in grants
    ):
        raise TypeError("grants must be a tuple of Grant objects")
    if len({grant.id for grant in grants}) != len(grants):
        raise ValueError("grant identifiers must be unique in the snapshot")

    def deny(reason: ReasonCode) -> AccessDecision:
        """Keep a refusal tied to the evaluated request without document contents."""
        return AccessDecision(request=request, allowed=False, reason=reason)

    denial = _scope_denial(
        company_id=request.company_id,
        project_id=request.project_id,
        employee_id=request.employee_id,
        agent_id=request.agent_id,
        action=request.action,
        company=company,
        project=project,
        employee=employee,
        agent=agent,
        delegation=delegation,
    )
    if denial is not None:
        return deny(denial)
    # The scope check above guarantees these entities are present.
    assert company is not None and project is not None and employee is not None

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
