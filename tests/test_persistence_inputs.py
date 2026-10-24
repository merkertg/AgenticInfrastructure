"""Verify the destructive-test database guard without opening a connection."""

import pytest

from tests.persistence.conftest import require_test_database_url


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://agentic@127.0.0.1:55435/agentic_test",
        "postgresql+psycopg://agentic@localhost/agentic_test",
        "postgresql+psycopg://agentic@[::1]/agentic_test",
    ],
)
def test_disposable_loopback_database_urls_are_accepted(url: str) -> None:
    """An explicit local test database may be reset by the integration fixtures."""
    assert require_test_database_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://agentic@127.0.0.1/agentic",
        "postgresql+psycopg://agentic@database.example/agentic_test",
        "postgresql://agentic@127.0.0.1/agentic_test",
        "sqlite:///agentic_test",
        "not a URL",
        "postgresql+psycopg://agentic@127.0.0.1/agentic_test?host=remote.example",
        "postgresql+psycopg://agentic@127.0.0.1/agentic_test?dbname=other_database",
        "postgresql+psycopg://agentic@127.0.0.1/agentic_test?service=external",
    ],
)
def test_nondisposable_or_remote_database_urls_are_rejected(url: str) -> None:
    """A naming or host mistake is rejected before any destructive SQL can run."""
    with pytest.raises(ValueError):
        require_test_database_url(url)
