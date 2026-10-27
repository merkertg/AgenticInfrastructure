"""Check context-search and intake entry points that need no database."""

import argparse
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.memory import UNAUTHORIZED, ContextBody
from app.domain.models import Action

CONTEXT_PATH = "/memory/projects/finance/context"


def test_context_route_requires_a_bearer_credential(client: TestClient) -> None:
    """Search without a credential receives the uniform challenge."""
    response = client.post(CONTEXT_PATH, json={"query": "travel"})
    assert response.status_code == 401
    assert response.json() == {"detail": UNAUTHORIZED}


@pytest.mark.parametrize(
    "payload",
    [
        {"query": "travel", "employee_id": "fin-user"},
        {"query": "travel", "project_id": "engineering"},
        {"query": ""},
        {"query": "!!!"},
        {"query": "x" * 257},
        {"query": "line\nbreak"},
        {"query": "travel", "max_items": 0},
        {"query": "travel", "max_items": 11},
        {"query": "travel", "max_items": "5"},
        {},
    ],
)
def test_context_body_rejects_scope_fields_and_invalid_queries(
    payload: dict[str, object],
) -> None:
    """The body carries only a bounded query and item count."""
    with pytest.raises(ValidationError):
        ContextBody.model_validate_json(json.dumps(payload))


def test_context_body_defaults_to_five_items() -> None:
    """A valid query without max_items uses the documented default."""
    assert ContextBody.model_validate_json('{"query": "travel"}').max_items == 5


def test_intake_grants_are_parsed_explicitly() -> None:
    """Grants name an employee and supported actions only."""
    from app.intake_lab import parse_grant

    grant = parse_grant("fin-user:read,write")
    assert grant.employee_id == "fin-user"
    assert grant.actions == frozenset({Action.READ, Action.WRITE})
    for value in ("fin-user", "fin-user:", ":read", "fin-user:admin"):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_grant(value)


def test_intake_lab_refuses_nonlab_targets_and_missing_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Import requires a local _lab database and existing files."""
    from app.intake_lab import main

    sample = tmp_path / "note.txt"
    sample.write_text("Fictional note.", encoding="utf-8")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as missing_url:
        main(["--project", "finance", str(sample)])
    assert missing_url.value.code == 2
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://agentic@127.0.0.1:5432/agentic_test"
    )
    with pytest.raises(SystemExit) as wrong_database:
        main(["--project", "finance", str(sample)])
    assert wrong_database.value.code == 2
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://agentic@127.0.0.1:5432/agentic_lab"
    )
    with pytest.raises(SystemExit) as missing_file:
        main(["--project", "finance", str(tmp_path / "absent.txt")])
    assert missing_file.value.code == 2


def test_intake_reads_at_most_one_byte_past_the_limit(tmp_path: Path) -> None:
    """Oversized files are not read entirely into memory."""
    from app.domain.text import MAX_BYTES
    from app.intake_lab import read_item

    large = tmp_path / "large.txt"
    large.write_bytes(b"a" * (MAX_BYTES * 3))
    item = read_item(large)
    assert len(item.raw) == MAX_BYTES + 1
    assert (item.document_id, item.source) == ("large", "file:large.txt")
