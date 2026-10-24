"""${message}"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    """Apply this revision's explicitly recorded schema changes."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Reverse this revision's explicitly recorded schema changes."""
    ${downgrades if downgrades else "pass"}
