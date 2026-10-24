"""Keep privileged fictional setup confined to an explicit local lab target."""

import pytest

from app.persistence.lab import validate_lab_url


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///agentic_lab",
        "postgresql+psycopg://user@remote.example/agentic_lab",
        "postgresql+psycopg://user@127.0.0.1/production",
        "postgresql+psycopg://user@127.0.0.1/agentic_lab?dbname=production",
        "postgresql+psycopg://user@127.0.0.1/agentic_lab?host=remote.example",
        "postgresql+psycopg://user@127.0.0.1/agentic_lab?service=production",
        "postgresql+psycopg:///agentic_lab",
    ],
)
def test_lab_setup_refuses_ambiguous_or_nonlocal_targets(url: str) -> None:
    """Lab fixtures must not be written through a remote or redirected URL."""
    with pytest.raises(ValueError):
        validate_lab_url(url)


def test_explicit_loopback_lab_target_is_accepted() -> None:
    """A dedicated loopback lab database can be selected without redirect options."""
    validate_lab_url("postgresql+psycopg://agentic@127.0.0.1:55435/agentic_lab")
