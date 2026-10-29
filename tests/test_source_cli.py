"""Check the source sync command's argument and environment guards offline."""

import argparse

import pytest

from app import source_sync
from app.source_sync import main, parse_account

LAB_URL = "postgresql+psycopg://agentic@127.0.0.1:1/agentic_lab"


def test_accounts_are_parsed() -> None:
    """PRINCIPAL=EMPLOYEE pairs are split and the principal is validated."""
    assert parse_account("u-fin=fin-user") == ("u-fin", "fin-user")
    for value in ("u-fin", "u-fin=", "=fin-user", "u fin=fin-user"):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_account(value)


@pytest.mark.parametrize(
    "database_url",
    ["", "postgresql+psycopg://agentic@127.0.0.1/agentic_test"],
)
def test_only_a_lab_database_is_accepted(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    """The command refuses a missing or non-lab database before connecting."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    with pytest.raises(SystemExit) as raised:
        main(["status", "handbook"])
    assert raised.value.code == 2


def test_run_requires_a_valid_token_and_never_echoes_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing or malformed token stops the run without printing the value."""
    monkeypatch.setenv("DATABASE_URL", LAB_URL)

    def refuse(*args: object, **kwargs: object) -> None:
        """Fail if the command tries to synchronize."""
        raise AssertionError("synchronize must not run")

    monkeypatch.setattr(source_sync, "synchronize", refuse)
    for token in (None, "short secret"):
        if token is None:
            monkeypatch.delenv("SOURCE_TOKEN", raising=False)
        else:
            monkeypatch.setenv("SOURCE_TOKEN", token)
        with pytest.raises(SystemExit) as raised:
            main(["run", "handbook"])
        assert raised.value.code == 2
        captured = capsys.readouterr()
        assert "short secret" not in captured.out + captured.err


def test_register_validates_settings_before_storing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An address outside the rules is refused before any database work."""
    monkeypatch.setenv("DATABASE_URL", LAB_URL)

    def refuse(*args: object, **kwargs: object) -> None:
        """Fail if the command tries to store the source."""
        raise AssertionError("register_source must not run")

    monkeypatch.setattr(source_sync, "register_source", refuse)
    with pytest.raises(SystemExit) as raised:
        main(
            [
                "register",
                "handbook",
                "--project",
                "finance",
                "--base-url",
                "http://handbook.alpha.example",
                "--space",
                "alpha-handbook",
                "--prefix",
                "hb-",
            ]
        )
    assert raised.value.code == 2
    assert "loopback" in capsys.readouterr().err
