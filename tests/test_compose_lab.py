"""Check the container scenario's guards offline; Docker is not started here."""

import subprocess

import pytest

from app.persistence.database import make_engine
from app.persistence.lab import seed_second_company, validate_lab_url
from scripts.compose_lab import Lab, main


@pytest.mark.parametrize("project", ["agentic-infrastructure", "production", "stage08"])
def test_only_disposable_projects_are_accepted(
    project: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scenario deletes its volume, so it refuses the normal lab project."""

    def refuse(*args: object, **kwargs: object) -> None:
        """Fail if the guard lets the scenario reach Docker."""
        raise AssertionError("docker must not run")

    monkeypatch.setattr(subprocess, "run", refuse)
    with pytest.raises(SystemExit) as raised:
        main(["--project", project])
    assert raised.value.code == 2


def test_lab_keeps_its_password_out_of_repr_and_on_loopback() -> None:
    """Each run gets a fresh password, a loopback lab URL and hidden secrets."""
    first, second = Lab("agentic-a", 18005, 55438), Lab("agentic-a", 18005, 55438)
    password = first.env["POSTGRES_PASSWORD"]
    assert password != second.env["POSTGRES_PASSWORD"]
    assert password not in repr(first)
    validate_lab_url(first.database_url)
    assert first.base_url == "http://127.0.0.1:18005"
    assert (first.env["APP_PORT"], first.env["POSTGRES_PORT"]) == ("18005", "55438")


def test_second_company_seed_requires_a_lab_database() -> None:
    """Beta is fictional lab data and cannot be written to another database."""
    engine = make_engine("postgresql+psycopg://agentic@127.0.0.1:1/agentic_test")
    with pytest.raises(ValueError):
        seed_second_company(engine)
