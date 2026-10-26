"""Provide isolated fictional inputs for the pure Python policy tests."""

import pytest

from tests.domain.fixtures import AccessCase, finance_case


@pytest.fixture
def case() -> AccessCase:
    """Create a fresh immutable snapshot for each policy or audit test."""
    return finance_case()
