"""Verify configuration defaults, source precedence, validation and caching."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings


def test_settings_have_local_defaults() -> None:
    """A missing optional .env file still permits a predictable local startup."""
    settings = Settings()
    assert settings.app_name == "agentic-infrastructure"
    assert settings.app_env == "development"
    assert settings.app_debug is False


def test_dotenv_overrides_defaults() -> None:
    """The local .env file can configure all current application settings."""
    Path(".env").write_text(
        "APP_NAME=from-file\nAPP_ENV=test\nAPP_DEBUG=true\n", encoding="utf-8"
    )
    settings = Settings()
    assert settings.app_name == "from-file"
    assert settings.app_env == "test"
    assert settings.app_debug is True


def test_environment_overrides_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Process variables override file values while unset fields retain file values."""
    Path(".env").write_text("APP_NAME=from-file\nAPP_ENV=test\n", encoding="utf-8")
    monkeypatch.setenv("APP_NAME", "from-environment")
    settings = Settings()
    assert settings.app_name == "from-environment"
    assert settings.app_env == "test"


def test_explicit_settings_override_environment_and_dotenv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Injected constructor values take precedence over both configuration sources."""
    Path(".env").write_text("APP_NAME=from-file\n", encoding="utf-8")
    monkeypatch.setenv("APP_NAME", "from-environment")
    assert Settings(app_name="explicit").app_name == "explicit"


def test_unrelated_dotenv_keys_are_ignored() -> None:
    """A shared .env file may contain keys unrelated to this application."""
    Path(".env").write_text("UNRELATED_SETTING=example\n", encoding="utf-8")
    assert Settings().app_name == "agentic-infrastructure"


@pytest.mark.parametrize("name", ["", " ", "\t\n"])
def test_empty_service_name_is_rejected(name: str) -> None:
    """Whitespace-only service names fail validation before the API starts."""
    with pytest.raises(ValidationError):
        Settings(app_name=name)


def test_service_name_is_trimmed() -> None:
    """Surrounding whitespace must not appear in service metadata or responses."""
    assert Settings(app_name="  demo-agent \t").app_name == "demo-agent"


@pytest.mark.parametrize("environment", ["development", "test", "production"])
def test_documented_environments_are_accepted(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    """Each documented environment name can be selected through process variables."""
    monkeypatch.setenv("APP_ENV", environment)
    assert Settings().app_env == environment


def test_invalid_environment_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A misspelled environment cannot silently select an unintended runtime mode."""
    monkeypatch.setenv("APP_ENV", "prodution")
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("value, expected", [("true", True), ("false", False)])
def test_debug_boolean_is_read_from_environment(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: bool
) -> None:
    """Explicit true and false values configure debug mode without string truthiness."""
    monkeypatch.setenv("APP_DEBUG", value)
    assert Settings().app_debug is expected


def test_invalid_debug_value_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """An invalid boolean fails startup rather than accidentally enabling debug."""
    monkeypatch.setenv("APP_DEBUG", "perhaps")
    with pytest.raises(ValidationError):
        Settings()


def test_validation_error_text_hides_rejected_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The printable validation error omits raw configuration input values."""
    rejected_value = "private-invalid-environment-value"
    monkeypatch.setenv("APP_ENV", rejected_value)
    with pytest.raises(ValidationError) as error:
        Settings()
    assert rejected_value not in str(error.value)


def test_settings_are_cached_until_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    """The process reuses one settings snapshot until its explicit cache reset."""
    first = get_settings()
    monkeypatch.setenv("APP_NAME", "updated")
    assert get_settings() is first
    assert get_settings().app_name == "agentic-infrastructure"
    get_settings.cache_clear()
    refreshed = get_settings()
    assert refreshed is not first
    assert refreshed.app_name == "updated"


def test_database_url_can_come_from_a_secret_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mounted file supplies the URL, trimmed, and stays secret in output."""
    url = "postgresql+psycopg://app:file-secret@db:5432/agentic_lab"
    Path("database_url").write_text(url + "\n", encoding="utf-8")
    monkeypatch.setenv("APP_DATABASE_URL_FILE", "database_url")
    settings = Settings()
    assert settings.app_database_url is not None
    assert settings.app_database_url.get_secret_value() == url
    assert "file-secret" not in repr(settings)


@pytest.mark.parametrize(
    "content",
    [b"", b"   \n", b"line-one\nline-two", b"\xff\xfe", b"x" * 5000],
)
def test_invalid_secret_files_are_rejected_without_echo(
    monkeypatch: pytest.MonkeyPatch, content: bytes
) -> None:
    """Empty, multi-line, non-UTF-8 or oversized files fail without their content."""
    Path("database_url").write_bytes(content)
    monkeypatch.setenv("APP_DATABASE_URL_FILE", "database_url")
    with pytest.raises(ValidationError) as error:
        Settings()
    assert "line-one" not in str(error.value)


def test_secret_file_must_exist_and_not_duplicate_the_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing file or two competing sources stop startup."""
    monkeypatch.setenv("APP_DATABASE_URL_FILE", "missing")
    with pytest.raises(ValidationError):
        Settings()
    Path("database_url").write_text("postgresql+psycopg://a@db/agentic_lab")
    monkeypatch.setenv("APP_DATABASE_URL_FILE", "database_url")
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql+psycopg://b@db/agentic_lab")
    with pytest.raises(ValidationError) as error:
        Settings()
    assert "not both" in str(error.value)
