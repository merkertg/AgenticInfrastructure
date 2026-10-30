"""Check the container scenario's guards offline; Docker is not started here."""

import stat
import subprocess
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from app.persistence.database import make_engine
from app.persistence.lab import seed_second_company, validate_lab_url
from scripts.compose_lab import ROOT, Lab, main
from scripts.lab_secrets import NAMES, write_secrets
from scripts.lab_secrets import main as lab_secrets_main


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


def test_lab_writes_private_secret_files_outside_the_repository() -> None:
    """Each run gets fresh secret files, loopback URLs and no password in repr."""
    first, second = Lab("agentic-a", 18005, 55438), Lab("agentic-a", 18005, 55438)
    try:
        directory = first.secrets.directory
        assert Path(first.env["AGENTIC_SECRETS_DIR"]) == directory
        assert ROOT not in directory.parents
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert first.secrets.owner_password != second.secrets.owner_password
        for secret in (first.secrets.owner_password, first.secrets.app_password):
            assert secret not in repr(first) and secret not in repr(first.secrets)
            assert all(secret not in value for value in first.env.values())
        validate_lab_url(first.database_url)
        assert make_url(first.app_database_url).username == "agentic_app"
        assert first.base_url == "http://127.0.0.1:18005"
    finally:
        first.remove_secrets()
        second.remove_secrets()
    assert not first.secrets.directory.exists()


def test_secret_files_are_complete_and_never_overwritten(tmp_path: Path) -> None:
    """The four files hold matching passwords; a second run refuses to replace them."""
    directory = tmp_path / "secrets"
    created = write_secrets(directory)
    files = {name: (directory / name).read_text().strip() for name in NAMES}
    assert files["postgres_password"] == created.owner_password
    assert files["app_db_password"] == created.app_password
    assert make_url(files["migrate_database_url"]).password == created.owner_password
    app_url = make_url(files["app_database_url"])
    assert (app_url.username, app_url.host, app_url.database) == (
        "agentic_app",
        "db",
        "agentic_lab",
    )
    assert app_url.password == created.app_password
    assert {stat.S_IMODE((directory / name).stat().st_mode) for name in NAMES} == {
        0o644
    }
    with pytest.raises(FileExistsError):
        write_secrets(directory)
    with pytest.raises(SystemExit):
        lab_secrets_main([str(directory)])


def test_second_company_seed_requires_a_lab_database() -> None:
    """Beta is fictional lab data and cannot be written to another database."""
    engine = make_engine("postgresql+psycopg://agentic@127.0.0.1:1/agentic_test")
    with pytest.raises(ValueError):
        seed_second_company(engine)
