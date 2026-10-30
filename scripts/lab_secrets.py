"""Write the Compose laboratory's secret files; values are never printed.

The directory is private (0700); files are 0644 so the database and service
users inside the containers can read their bind-mounted copies. Existing files
are never overwritten: the database keeps the passwords of its first start.
"""

import argparse
import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path

OWNER = "agentic"
APP_LOGIN = "agentic_app"
DATABASE = "agentic_lab"
NAMES = (
    "postgres_password",
    "app_db_password",
    "migrate_database_url",
    "app_database_url",
)


@dataclass(frozen=True, slots=True)
class LabSecrets:
    """Hold the generated passwords for this process; repr shows the folder only."""

    directory: Path
    owner_password: str = field(repr=False)
    app_password: str = field(repr=False)

    def host_url(self, port: int, *, app: bool = False) -> str:
        """Build a loopback URL for host lab tools, as owner or as the service."""
        user, password = (
            (APP_LOGIN, self.app_password)
            if app
            else (
                OWNER,
                self.owner_password,
            )
        )
        return f"postgresql+psycopg://{user}:{password}@127.0.0.1:{port}/{DATABASE}"


def write_secrets(directory: Path) -> LabSecrets:
    """Create fresh URL-safe passwords and the four secret files Compose mounts."""
    if any((directory / name).exists() for name in NAMES):
        raise FileExistsError("secret files already exist; reuse or remove them")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    owner, app = secrets.token_hex(24), secrets.token_hex(24)
    values = {
        "postgres_password": owner,
        "app_db_password": app,
        "migrate_database_url": (
            f"postgresql+psycopg://{OWNER}:{owner}@db:5432/{DATABASE}"
        ),
        "app_database_url": f"postgresql+psycopg://{APP_LOGIN}:{app}@db:5432/{DATABASE}",
    }
    for name, value in values.items():
        path = directory / name
        path.write_text(value + "\n", encoding="utf-8")
        path.chmod(0o644)
    return LabSecrets(directory=directory, owner_password=owner, app_password=app)


def main(argv: list[str] | None = None) -> int:
    """Create the secrets folder for the manual Compose lab and list the files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?", default=Path("var/secrets"))
    args = parser.parse_args(argv)
    try:
        write_secrets(args.directory)
    except FileExistsError as error:
        parser.error(str(error))
    print(json.dumps({"directory": str(args.directory), "files": list(NAMES)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
