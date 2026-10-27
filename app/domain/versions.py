"""Name document revisions deterministically from their company, document and number."""

from uuid import NAMESPACE_URL, uuid5

VERSION_NAMESPACE = uuid5(NAMESPACE_URL, "urn:agentic-infrastructure:memory-version")


def derive_version_id(company_id: str, document_id: str, number: int) -> str:
    """Name a revision deterministically so an identical retry keeps its fingerprint."""
    return str(uuid5(VERSION_NAMESPACE, f"{company_id}/{document_id}/{number}"))
