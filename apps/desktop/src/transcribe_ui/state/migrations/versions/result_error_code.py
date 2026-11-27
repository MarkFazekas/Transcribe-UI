"""Stored code of the job failures the desktop decides.

The job payload keeps the code of `unreadable result` and `missing result` (`result_error_code`),
so the frontends show the failure in their language (architecture §3.1 "Language of the texts").
Rows stored before get the code of those two texts and null otherwise: the worker writes its
errors as `Type: message`, so its texts never equal them (architecture §3.8).

Revision ID: c41f8e2a6d93
Revises: b7e2c4a91f03
"""

from alembic import op

# Revision identifiers, used by Alembic.
revision = "c41f8e2a6d93"
down_revision = "b7e2c4a91f03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the key to every stored job: the code of the desktop's two texts, null otherwise."""
    op.execute(
        "UPDATE jobs SET payload = json_insert(payload, '$.result_error_code', "
        "CASE json_extract(payload, '$.result_error') "
        "WHEN 'unreadable result' THEN 'result_unreadable' "
        "WHEN 'missing result' THEN 'result_missing' "
        "ELSE NULL END)",
    )


def downgrade() -> None:
    """Remove the key again (never run by the app: a newer database is refused instead)."""
    op.execute("UPDATE jobs SET payload = json_remove(payload, '$.result_error_code')")
