"""Several installations on one bucket.

A batch adopted from another computer's manifest keeps its uploader in the batch payload
(`adopted`); every stored batch gets the key as null. A `ready` batch that another computer
removed from the cloud is marked by the new column `removed_from_cloud` (false for every stored
batch). The manifest is no longer held back during a run (a run processes only the batches its
lease lists), so `manifest_held` goes: a batch still held at the upgrade stays `uploading` and is
published when its upload is resumed with `run_upload` (architecture §3.9 "Run scope", §3.8).
The new table `known_leases` keeps the live run leases the last `refresh_status` read (empty until
the first refresh).

Revision ID: 8e5b2d7c1f40
Revises: 609bed4e68d4
"""

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic.
revision = "8e5b2d7c1f40"
down_revision = "609bed4e68d4"
branch_labels = None
depends_on = None


def _flag(name: str) -> "sa.Column[bool]":
    """Return a boolean column that is false for every stored row."""
    stored_false: sa.TextClause = sa.text("0")
    return sa.Column(name, sa.Boolean(), nullable=False, server_default=stored_false)


def upgrade() -> None:
    """Add `removed_from_cloud` (false), drop `manifest_held`, add the payload key `adopted` (null) and `known_leases`."""
    with op.batch_alter_table("batches") as batch:
        batch.add_column(_flag("removed_from_cloud"))
        batch.drop_column("manifest_held")
    op.execute("UPDATE batches SET payload = json_insert(payload, '$.adopted', NULL)")
    op.create_table(
        "known_leases",
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("batch_ids", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("until", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_known_leases")),
    )


def downgrade() -> None:
    """Drop `known_leases`, restore `manifest_held` (false), drop `removed_from_cloud` and the key (never run by the app)."""
    op.drop_table("known_leases")
    op.execute("UPDATE batches SET payload = json_remove(payload, '$.adopted')")
    with op.batch_alter_table("batches") as batch:
        batch.add_column(_flag("manifest_held"))
        batch.drop_column("removed_from_cloud")
