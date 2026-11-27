"""Stored batch composition and run report.

The batch payload keeps the selection it was composed from (`composition`); the run payload keeps
the worker report, the deadline stop's error, the time of the last refresh and of a queued pod's
acknowledged cancel (`worker`, `stop_error`, `refreshed_at`, `cancel_acknowledged_at`). Rows stored before have none of them: the keys are added as null,
so every stored payload has the shape the app writes (architecture §3.8).

Revision ID: b7e2c4a91f03
Revises: 5d0c3b1e7a42
"""

from alembic import op

# Revision identifiers, used by Alembic.
revision = "b7e2c4a91f03"
down_revision = "5d0c3b1e7a42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the new payload keys as null to every stored batch and run (keys already there are kept)."""
    op.execute("UPDATE batches SET payload = json_insert(payload, '$.composition', NULL)")
    op.execute(
        "UPDATE runs SET payload = json_insert("
        "payload, '$.worker', NULL, '$.stop_error', NULL, '$.refreshed_at', NULL, '$.cancel_acknowledged_at', NULL)",
    )


def downgrade() -> None:
    """Remove the keys again (never run by the app: a newer database is refused instead)."""
    op.execute("UPDATE batches SET payload = json_remove(payload, '$.composition')")
    op.execute(
        "UPDATE runs SET payload = json_remove(payload, '$.worker', '$.stop_error', '$.refreshed_at', '$.cancel_acknowledged_at')",
    )
