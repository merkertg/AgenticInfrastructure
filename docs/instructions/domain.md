# Offline domain and access policy

[README](../../README.md) · [Detailed Russian guide](../STAGE_02.md) · [ADR](../adr/0002-domain-memory-access-policy.md)

Run the fictional demonstration from the repository root:

```bash
uv sync --locked
uv run --locked python -m app.demo
uv run --locked pytest tests/domain
```

The demo imports the domain, not the web application or settings. No server,
`.env`, network, database or credentials are required. All cases use fixed
fictional objects and a supplied timestamp.

## Contract

`app/domain/models.py` defines immutable dataclasses and explicit action/reason
enums. `app/domain/policy.py` evaluates the supplied snapshot:

```python
decision = evaluate_access(
    request,
    company=company,
    project=project,
    employee=employee,
    agent=agent,
    delegation=delegation,
    document=document,
    version=version,
    grants=(grant,),
)
```

An allowed decision requires an active, matching company/project, employee,
employee-bound agent, delegation, document and version. The requested action must
be in both the delegation and a current matching employee document grant.
`Action.READ` and `Action.WRITE` are independent. A missing or revoked grant
does not inherit access from another employee, document or project.

The function returns an `AccessDecision` containing the exact request, a boolean
outcome and a `ReasonCode`. It returns no document content and performs no
mutation. Missing or inactive prerequisites deny access. Malformed snapshot types
or duplicate grant identifiers raise an input error; they do not authorize a
request. The function performs no authentication or lookup of current rights.

Use `dataclasses.replace()` to create a changed immutable snapshot, then call the
policy again. An old allowed decision is not a reusable permission token.

## Audit-attempt model

```python
event = make_audit_event(decision, occurred_at=aware_timestamp)
```

`app/domain/audit.py` copies request/outcome metadata and normalizes an explicit
timezone-aware `datetime` to UTC. A naive datetime is rejected. No clock is read
and no event is written to disk or a database. The event describes an
authorization attempt, not a completed read, write or delivered response.

Identifiers in these offline models are input metadata. They do not prove the
employee's identity, the authenticity of the agent registration or the freshness
of grants. Trusted loading, durable audit and authenticated HTTP operations
belong to later stages.

`DocumentVersion.content` is bounded and excluded from `repr()`, but the field
is still accessible to ordinary Python code. Never treat this convenience as a
redaction or access-control boundary. Use only fictional data in this stage.
