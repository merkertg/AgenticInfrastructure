"""Load verified results for the PDF guide without importing ReportLab."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class GuideDataError(ValueError):
    """Explain why verification data cannot be shown in the guide."""


@dataclass(frozen=True, slots=True)
class Publication:
    """Summarize one published stage: commit, remote ref and CI evidence."""

    name: str
    commit: str
    remote_ref: str
    ci_run: str
    ci_result: str


@dataclass(frozen=True, slots=True)
class GuideFacts:
    """Hold only observed numbers and statuses that the guide prints."""

    stage: int
    checked_at: str
    base_commit: str
    tests_total: int
    tests_unit: int
    tests_postgres: int
    skipped_without_database: int
    mypy_files: int
    local_links: int
    markdown_files: int
    lock_sha256: str
    publications: tuple[Publication, ...]
    not_implemented: tuple[str, ...]
    limitations: tuple[str, ...]
    documentation_note: str


def _get(data: dict[str, Any], *keys: str) -> Any:
    """Read a nested key or fail with the missing path."""
    value: Any = data
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            raise GuideDataError(f"missing {'.'.join(keys)}")
        value = value[key]
    return value


def _count(data: dict[str, Any], *keys: str) -> int:
    """Read a non-negative integer counter."""
    value = _get(data, *keys)
    if type(value) is not int or value < 0:
        raise GuideDataError(f"{'.'.join(keys)} must be a non-negative integer")
    return value


def _publication(name: str, entry: dict[str, Any]) -> Publication:
    """Normalize a publication record and require an explicit CI status."""
    if "github_actions" not in entry or "remote_ref" not in entry:
        raise GuideDataError(f"{name} needs explicit remote_ref and github_actions")
    actions = entry["github_actions"]
    if isinstance(actions, dict):
        ci_run = str(actions.get("run_id", "—"))
        ci_result = f"{actions.get('status', '?')} / {actions.get('conclusion', '?')}"
    elif isinstance(actions, str):
        ci_run, ci_result = "—", actions
    else:
        raise GuideDataError(f"{name}.github_actions must be an object or a status")
    commit = entry.get("commit") or entry.get("main_commit") or "—"
    return Publication(
        name=name,
        commit=str(commit),
        remote_ref=str(entry["remote_ref"]),
        ci_run=ci_run,
        ci_result=ci_result,
    )


def load_facts(path: Path) -> GuideFacts:
    """Validate verification.json and return the facts printed in the guide."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GuideDataError(f"cannot read {path}: {error}") from None
    if not isinstance(data, dict):
        raise GuideDataError("verification data must be a JSON object")
    total = _count(data, "checks", "pytest", "tests_passed")
    unit = _count(data, "checks", "pytest", "without_postgres", "passed")
    postgres = _count(data, "checks", "pytest", "postgres", "passed")
    if total != unit + postgres:
        raise GuideDataError("tests_passed must equal unit plus PostgreSQL tests")
    stage = str(_get(data, "stage"))
    if not stage.isdigit():
        raise GuideDataError("stage must be a stage number")
    publications = _get(data, "publication")
    if not isinstance(publications, dict) or not publications:
        raise GuideDataError("publication must list at least one entry")
    lists: dict[str, tuple[str, ...]] = {}
    for key in ("not_implemented", "limitations"):
        values = _get(data, key)
        if not isinstance(values, list) or not all(
            isinstance(item, str) for item in values
        ):
            raise GuideDataError(f"{key} must be a list of strings")
        lists[key] = tuple(values)
    documentation = data.get("documentation_update", {})
    if not isinstance(documentation, dict):
        raise GuideDataError("documentation_update must be an object")
    return GuideFacts(
        stage=int(stage),
        checked_at=str(_get(data, "checked_at")),
        base_commit=str(_get(data, "revision", "base_commit")),
        tests_total=total,
        tests_unit=unit,
        tests_postgres=postgres,
        skipped_without_database=_count(
            data, "checks", "pytest", "without_test_database_url", "skipped"
        ),
        mypy_files=_count(data, "checks", "mypy", "source_files"),
        local_links=_count(data, "checks", "documentation_links", "local_links"),
        markdown_files=_count(data, "checks", "documentation_links", "markdown_files"),
        lock_sha256=str(_get(data, "lock", "sha256")),
        publications=tuple(
            _publication(name, entry)
            for name, entry in publications.items()
            if isinstance(entry, dict)
        ),
        not_implemented=lists["not_implemented"],
        limitations=lists["limitations"],
        documentation_note=str(
            data.get("documentation_update", {}).get("guide_note", "")
        ),
    )
