"""Check memory API contracts that need no database: credentials, inputs and IDs."""

import re

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.api.memory import (
    IDENTIFIER,
    NOT_CONFIGURED,
    UNAUTHORIZED,
    AppendVersionBody,
    derive_version_id,
)
from app.config import Settings
from app.persistence.credentials import (
    CredentialAuthenticator,
    generate_token,
    hash_token,
)
from app.persistence.database import make_engine
from app.persistence.lab import validate_lab_url

READ_PATH = "/memory/projects/finance/documents/policy/versions/policy-v1"
APPEND_PATH = "/memory/projects/finance/documents/policy/versions"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic YWdlbnQ6c2VjcmV0"},
        {"Authorization": "Bearer"},
        {"X-Employee-ID": "fin-user", "X-Agent-ID": "fin-agent"},
    ],
)
def test_memory_routes_require_a_bearer_credential(
    client: TestClient, headers: dict[str, str]
) -> None:
    """Missing credentials and identity headers get one uniform challenge."""
    for response in (
        client.get(READ_PATH, headers=headers),
        client.post(APPEND_PATH, headers=headers, json={}),
    ):
        assert response.status_code == 401
        assert response.json() == {"detail": UNAUTHORIZED}
        assert response.headers["www-authenticate"] == "Bearer"


def test_memory_routes_fail_closed_without_storage(client: TestClient) -> None:
    """A bearer token cannot be checked when no database URL is configured."""
    response = client.get(
        READ_PATH, headers={"Authorization": f"Bearer {generate_token()}"}
    )
    assert response.status_code == 503
    assert response.json() == {"detail": NOT_CONFIGURED}


def test_health_stays_independent_of_unreachable_storage() -> None:
    """Configuring storage builds a lazy engine; liveness does not connect to it."""
    from app.main import create_app

    settings = Settings(
        app_database_url=SecretStr(
            "postgresql+psycopg://agentic@127.0.0.1:1/unreachable_test"
        )
    )
    with TestClient(create_app(settings)) as test_client:
        assert test_client.get("/health").json()["status"] == "ok"


def test_storage_url_is_secret_and_validated() -> None:
    """The URL is hidden from settings output and non-PostgreSQL URLs are refused."""
    from app.main import create_app

    url = "postgresql+psycopg://agentic:lab-secret@127.0.0.1:5432/agentic_lab"
    settings = Settings(app_database_url=SecretStr(url))
    assert "lab-secret" not in repr(settings)
    with pytest.raises(ValueError, match="postgresql\\+psycopg"):
        create_app(Settings(app_database_url=SecretStr("sqlite:///memory.db")))


def test_storage_url_is_read_from_the_app_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The web service reads only the explicit APP_DATABASE_URL variable."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://agentic@127.0.0.1/a_lab")
    assert Settings().app_database_url is None
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql+psycopg://agentic@127.0.0.1/b")
    configured = Settings().app_database_url
    assert configured is not None
    assert configured.get_secret_value().endswith("/b")


def test_generated_tokens_are_unique_and_hash_to_sha256() -> None:
    """Issued secrets have the documented shape and only their digest is derived."""
    first, second = generate_token(), generate_token()
    assert first != second
    assert re.fullmatch(r"aik1_[A-Za-z0-9_-]{43}", first)
    digest = hash_token(first)
    assert digest is not None and re.fullmatch(r"[a-f0-9]{64}", digest)
    assert hash_token(first) == digest != hash_token(second)
    assert first not in digest


@pytest.mark.parametrize(
    "token",
    [
        "",
        "aik1_short",
        "aik2_" + "a" * 43,
        "aik1_" + "a" * 42 + "!",
        " aik1_" + "a" * 43,
        "aik1_" + "a" * 44,
        None,
        123,
    ],
)
def test_malformed_tokens_have_no_digest(token: object) -> None:
    """Unexpected token shapes are rejected before any database lookup."""
    assert hash_token(token) is None


def test_malformed_token_is_refused_without_connecting() -> None:
    """Authentication of a malformed value never touches the unreachable database."""
    from datetime import UTC, datetime

    engine = make_engine("postgresql+psycopg://agentic@127.0.0.1:1/unreachable_test")
    authenticator = CredentialAuthenticator(engine)
    assert authenticator.authenticate("not-a-token", at=datetime.now(UTC)) is None
    with pytest.raises(ValueError, match="timezone-aware"):
        authenticator.authenticate(generate_token(), at=datetime(2026, 10, 24))


def test_derived_version_ids_are_stable_valid_and_scoped() -> None:
    """Retries get the same ID; another number, document or company does not."""
    first = derive_version_id("alpha", "policy", 2)
    assert first == derive_version_id("alpha", "policy", 2)
    assert re.fullmatch(IDENTIFIER, first)
    assert (
        len(
            {
                first,
                derive_version_id("alpha", "policy", 3),
                derive_version_id("alpha", "plan", 2),
                derive_version_id("beta", "policy", 2),
            }
        )
        == 4
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"base_version_id": "v1", "expected_number": 1, "content": "x", "x": 1},
        {
            "base_version_id": "v1",
            "expected_number": 1,
            "content": "x",
            "employee_id": "fin-user",
        },
        {"base_version_id": "v1", "expected_number": "1", "content": "x"},
        {"base_version_id": "v1", "expected_number": 0, "content": "x"},
        {"base_version_id": "v1", "expected_number": 1_000_000, "content": "x"},
        {"base_version_id": "v1", "expected_number": 1, "content": ""},
        {"base_version_id": "v1", "expected_number": 1, "content": "x" * 20_001},
        {"base_version_id": "v1", "expected_number": 1, "content": "a\x00b"},
        {"base_version_id": "../v1", "expected_number": 1, "content": "x"},
        {"expected_number": 1, "content": "x"},
    ],
)
def test_append_body_rejects_actor_fields_and_invalid_revisions(
    payload: dict[str, object],
) -> None:
    """The body cannot carry identity or provenance and bounds revision data."""
    with pytest.raises(ValidationError):
        AppendVersionBody.model_validate_json(_json(payload))


def test_append_body_accepts_a_bounded_revision() -> None:
    """A valid body contains only the base version, expected number and text."""
    body = AppendVersionBody.model_validate_json(
        _json({"base_version_id": "policy-v1", "expected_number": 1, "content": "x"})
    )
    assert body.model_dump() == {
        "base_version_id": "policy-v1",
        "expected_number": 1,
        "content": "x",
    }


def test_openapi_declares_bearer_security_and_no_identity_inputs(
    client: TestClient,
) -> None:
    """Generated documentation exposes a bearer scheme and no actor parameters."""
    schema = client.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    body = schema["components"]["schemas"]["AppendVersionBody"]
    assert set(body["properties"]) == {"base_version_id", "expected_number", "content"}
    for path in schema["paths"].values():
        for operation in path.values():
            names = {parameter["name"] for parameter in operation.get("parameters", [])}
            assert not names & {"employee_id", "agent_id", "company_id"}


def test_credential_lab_refuses_nonlab_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credential issuance requires an explicit local database ending in _lab."""
    from app.credential_lab import main

    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as missing:
        main(["fin-agent"])
    assert missing.value.code == 2
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://agentic@10.0.0.5:5432/agentic_lab"
    )
    with pytest.raises(SystemExit) as remote:
        main(["fin-agent"])
    assert remote.value.code == 2
    with pytest.raises(ValueError):
        validate_lab_url("postgresql+psycopg://agentic@10.0.0.5:5432/agentic_lab")


@pytest.mark.parametrize("hours", ["0", "169"])
def test_credential_lab_bounds_lifetime(
    monkeypatch: pytest.MonkeyPatch, hours: str
) -> None:
    """Lab credentials must expire within one week."""
    from app.credential_lab import main

    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://agentic@127.0.0.1:5432/agentic_lab"
    )
    with pytest.raises(SystemExit) as refused:
        main(["fin-agent", "--hours", hours])
    assert refused.value.code == 2


def _json(payload: dict[str, object]) -> str:
    """Serialize a request body exactly as an HTTP client would send it."""
    import json

    return json.dumps(payload)
