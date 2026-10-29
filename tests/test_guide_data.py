"""Check validation of the verification data printed in the PDF guide."""

import json
from pathlib import Path

import pytest

from scripts.guide_data import GuideDataError, load_facts

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/guide/verification.json"


def test_current_verification_data_is_valid() -> None:
    """The committed JSON satisfies the guide's consistency rules."""
    facts = load_facts(SOURCE)
    assert facts.tests_total == facts.tests_unit + facts.tests_postgres
    assert facts.publications


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("checks", "pytest", "tests_passed"), 1),
        (("checks", "pytest", "postgres", "passed"), -1),
        (("stage",), "five"),
        (("limitations",), "not a list"),
        (("guide_note",), 5),
    ],
)
def test_inconsistent_data_is_rejected(
    tmp_path: Path, path: tuple[str, ...], value: object
) -> None:
    """Mismatched totals, negative counters and wrong types stop the build."""
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    broken = tmp_path / "verification.json"
    broken.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(GuideDataError):
        load_facts(broken)


def test_publication_needs_an_explicit_ci_status(tmp_path: Path) -> None:
    """A publication without a GitHub Actions status is not accepted."""
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    del data["publication"]["stage_05"]["github_actions"]
    broken = tmp_path / "verification.json"
    broken.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(GuideDataError, match="github_actions"):
        load_facts(broken)
