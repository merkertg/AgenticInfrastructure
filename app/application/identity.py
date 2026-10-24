"""Carry a server-authenticated actor separately from caller-supplied request data."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Principal:
    """Identify a verified agent credential and the employee its registration serves."""

    company_id: str
    employee_id: str
    agent_id: str
    credential_id: str
