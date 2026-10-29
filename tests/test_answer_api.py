"""Check the answer route's entry contract without a database."""

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.answers import AnswerBody
from app.api.memory import NOT_CONFIGURED, UNAUTHORIZED
from app.persistence.credentials import generate_token

ANSWER_PATH = "/memory/projects/finance/answers"


def test_answer_route_requires_a_bearer_credential(client: TestClient) -> None:
    """Questions without a credential receive the uniform challenge."""
    response = client.post(ANSWER_PATH, json={"question": "travel limit"})
    assert response.status_code == 401
    assert response.json() == {"detail": UNAUTHORIZED}


def test_answer_route_fails_closed_without_storage(client: TestClient) -> None:
    """No model runs when the credential and scope cannot be checked."""
    response = client.post(
        ANSWER_PATH,
        headers={"Authorization": f"Bearer {generate_token()}"},
        json={"question": "travel limit"},
    )
    assert response.status_code == 503
    assert response.json() == {"detail": NOT_CONFIGURED}


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "travel", "project_id": "engineering"},
        {"question": "travel", "employee_id": "eng-user"},
        {"question": "travel", "provider": "live"},
        {"question": ""},
        {"question": "???"},
        {"question": "two\nlines"},
        {"question": "x" * 501},
        {"question": 5},
        {},
    ],
)
def test_answer_body_rejects_scope_fields_and_invalid_questions(
    payload: dict[str, object],
) -> None:
    """The body carries one bounded question and nothing that selects scope."""
    with pytest.raises(ValidationError):
        AnswerBody.model_validate_json(json.dumps(payload))


def test_answer_body_accepts_a_cyrillic_question() -> None:
    """Questions may use any Unicode words on one printable line."""
    body = AnswerBody.model_validate_json('{"question": "Какой лимит?"}')
    assert body.question == "Какой лимит?"
