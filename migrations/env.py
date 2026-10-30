"""Run migrations against an explicit connection, DATABASE_URL or its secret file."""

import os
from pathlib import Path

from alembic import context
from sqlalchemy import Connection

from app.config import read_secret_file
from app.persistence.database import make_engine
from app.persistence.schema import metadata

config = context.config
target_metadata = metadata


def run_migrations_offline() -> None:
    """Generate PostgreSQL SQL without opening a database connection."""
    context.configure(
        dialect_name="postgresql",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_with_connection(connection: Connection) -> None:
    """Apply the revision chain using an existing transaction-capable connection."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Use a supplied connection or build one from an explicit environment URL."""
    supplied = config.attributes.get("connection")
    if supplied is not None:
        if not isinstance(supplied, Connection):
            raise TypeError("Alembic connection must be a SQLAlchemy Connection")
        run_with_connection(supplied)
        return
    database_url = os.environ.get("DATABASE_URL")
    url_file = os.environ.get("DATABASE_URL_FILE")
    if url_file:
        if database_url:
            raise ValueError("set DATABASE_URL or DATABASE_URL_FILE, not both")
        database_url = read_secret_file(Path(url_file))
    if not database_url:
        raise ValueError("set DATABASE_URL or DATABASE_URL_FILE before migrating")
    engine = make_engine(database_url)
    try:
        with engine.begin() as connection:
            run_with_connection(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
