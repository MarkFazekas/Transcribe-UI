"""${message}.

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}
# Revision identifiers, used by Alembic.
revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    """Upgrade the schema."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Downgrade the schema (never run by the app: a newer database is refused instead)."""
    ${downgrades if downgrades else "pass"}
