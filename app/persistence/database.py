"""Create explicit PostgreSQL connections and apply versioned migrations."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]


def make_engine(database_url: str) -> Engine:
    """Build a quiet PostgreSQL engine without connecting until first use."""
    if not isinstance(database_url, str):
        raise TypeError("database_url must be a string")
    url = make_url(database_url)
    if url.drivername != "postgresql+psycopg":
        raise ValueError("database_url must use postgresql+psycopg")
    if not url.database:
        raise ValueError("database_url must select an explicit database")
    return create_engine(
        url,
        echo=False,
        hide_parameters=True,
        isolation_level="READ COMMITTED",
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": 5,
            "options": "-c statement_timeout=5000 -c lock_timeout=5000",
        },
    )


def migrate(database_url: str) -> None:
    """Upgrade the explicit database through Alembic, closing its engine afterward."""
    engine = make_engine(database_url)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()
