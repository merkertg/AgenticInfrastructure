"""Create fictional lab records only in an explicitly selected local lab database."""

from sqlalchemy import Engine, func, insert, select
from sqlalchemy.engine import make_url

from app.persistence import schema as db


def validate_lab_url(database_url: str) -> None:
    """Refuse nonlocal, redirected or nonspecific targets for fixture creation."""
    url = make_url(database_url)
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in {"127.0.0.1", "localhost", "::1"}
        or not url.database
        or not url.database.endswith("_lab")
        or url.query
    ):
        raise ValueError("use a local postgresql+psycopg database ending in _lab")


def seed_lab(engine: Engine) -> None:
    """Populate an empty lab once as privileged setup, outside agent operations."""
    validate_lab_url(engine.url.render_as_string(hide_password=False))
    with engine.begin() as connection:
        if connection.scalar(select(func.count()).select_from(db.companies)) != 0:
            raise ValueError("lab setup requires an empty database")
        connection.execute(insert(db.companies).values(id="alpha", name="Alpha"))
        connection.execute(
            insert(db.projects),
            [
                {"company_id": "alpha", "id": "finance", "name": "Finance"},
                {"company_id": "alpha", "id": "engineering", "name": "Engineering"},
            ],
        )
        connection.execute(
            insert(db.employees),
            [
                {"company_id": "alpha", "id": "fin-user", "name": "Fictional analyst"},
                {"company_id": "alpha", "id": "eng-user", "name": "Fictional engineer"},
            ],
        )
        connection.execute(
            insert(db.agents),
            [
                {
                    "company_id": "alpha",
                    "id": "fin-agent",
                    "employee_id": "fin-user",
                    "name": "Finance assistant",
                },
                {
                    "company_id": "alpha",
                    "id": "eng-agent",
                    "employee_id": "eng-user",
                    "name": "Engineering assistant",
                },
            ],
        )
        connection.execute(
            insert(db.delegations),
            [
                {
                    "company_id": "alpha",
                    "id": "fin-scope",
                    "project_id": "finance",
                    "employee_id": "fin-user",
                    "agent_id": "fin-agent",
                    "actions": ["read", "write"],
                },
                {
                    "company_id": "alpha",
                    "id": "eng-scope",
                    "project_id": "engineering",
                    "employee_id": "eng-user",
                    "agent_id": "eng-agent",
                    "actions": ["read"],
                },
            ],
        )
        connection.execute(
            insert(db.documents).values(
                company_id="alpha",
                id="policy",
                project_id="finance",
                title="Fictional finance policy",
            )
        )
        connection.execute(
            insert(db.versions).values(
                company_id="alpha",
                id="policy-v1",
                document_id="policy",
                number=1,
                source="fixture:policy",
                content="Fictional revision one.",
            )
        )
        connection.execute(
            insert(db.grants).values(
                company_id="alpha",
                id="fin-grant",
                project_id="finance",
                employee_id="fin-user",
                document_id="policy",
                actions=["read", "write"],
            )
        )
