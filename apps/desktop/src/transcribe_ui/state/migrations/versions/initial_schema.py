"""Initial schema.

Until M5 is marked (built), this one revision is edited in place (delete the local database
after a change); from then on every schema change is a new revision (plan m5 §1).

Revision ID: 5d0c3b1e7a42
Revises:
"""

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic.
revision = "5d0c3b1e7a42"
down_revision = None
branch_labels = None
depends_on = None


def _payload() -> sa.Column[object]:
    return sa.Column("payload", sa.JSON(), nullable=False)


def _timestamp(name: str) -> sa.Column[object]:
    return sa.Column(name, sa.DateTime(), nullable=False)


def _status(name: str) -> sa.Column[object]:
    return sa.Column(name, sa.String(length=32), nullable=False)


def _batch_ref(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["batch_id"],
        ["batches.batch_id"],
        name=op.f(f"fk_{table}_batch_id_batches"),
        ondelete="CASCADE",
    )


def _item_ref(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["item_id"],
        ["items.item_id"],
        name=op.f(f"fk_{table}_item_id_items"),
        ondelete="CASCADE",
    )


def _job_ref(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["job_id"],
        ["jobs.job_id"],
        name=op.f(f"fk_{table}_job_id_jobs"),
        ondelete="CASCADE",
    )


def _indexes(table: str, *columns: str) -> None:
    for column in columns:
        name = op.f(f"ix_{table}_{column}")
        op.create_index(name, table, [column], unique=False)


def _create_batches_and_runs() -> None:
    op.create_table(
        "batches",
        sa.Column("batch_id", sa.String(length=40), nullable=False),
        _status("phase"),
        sa.Column("manifest_held", sa.Boolean(), nullable=False),
        sa.Column("audio_deleted", sa.Boolean(), nullable=False),
        sa.Column("hidden", sa.Boolean(), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _payload(),
        sa.PrimaryKeyConstraint("batch_id", name=op.f("pk_batches")),
    )
    _indexes("batches", "hidden", "phase")
    op.create_table(
        "probe_cache",
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mtime_ns", sa.BigInteger(), nullable=False),
        _timestamp("probed_at"),
        _payload(),
        sa.PrimaryKeyConstraint("path", "size_bytes", "mtime_ns", name=op.f("pk_probe_cache")),
    )
    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(length=26), nullable=False),
        _status("status"),
        sa.Column("pod_name", sa.String(), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _payload(),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_runs")),
        sa.UniqueConstraint("pod_name", name=op.f("uq_runs_pod_name")),
    )
    _indexes("runs", "status")


def _create_items_and_objects() -> None:
    op.create_table(
        "items",
        sa.Column("item_id", sa.String(length=26), nullable=False),
        sa.Column("batch_id", sa.String(length=40), nullable=False),
        _status("phase"),
        _payload(),
        _batch_ref("items"),
        sa.PrimaryKeyConstraint("item_id", name=op.f("pk_items")),
    )
    _indexes("items", "batch_id", "phase")
    op.create_table(
        "audio_objects",
        sa.Column("object_key", sa.String(), nullable=False),
        sa.Column("batch_id", sa.String(length=40), nullable=False),
        sa.Column("item_id", sa.String(length=26), nullable=False),
        _status("status"),
        _payload(),
        _batch_ref("audio_objects"),
        _item_ref("audio_objects"),
        sa.PrimaryKeyConstraint("object_key", name=op.f("pk_audio_objects")),
    )
    _indexes("audio_objects", "batch_id", "item_id", "status")


def _create_jobs() -> None:
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("item_id", sa.String(length=26), nullable=False),
        sa.Column("batch_id", sa.String(length=40), nullable=False),
        sa.Column("language", sa.String(length=3), nullable=False),
        _status("kind"),
        _status("status"),
        _payload(),
        _batch_ref("jobs"),
        _item_ref("jobs"),
        sa.PrimaryKeyConstraint("job_id", name=op.f("pk_jobs")),
        sa.UniqueConstraint("item_id", "language", name=op.f("uq_jobs_item_id_language")),
    )
    _indexes("jobs", "batch_id", "item_id", "status")
    op.create_table(
        "extracted_subtitles",
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("srt_text", sa.Text(), nullable=False),
        _payload(),
        _job_ref("extracted_subtitles"),
        sa.PrimaryKeyConstraint("job_id", name=op.f("pk_extracted_subtitles")),
    )
    op.create_table(
        "placements",
        sa.Column("placement_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.String(length=40), nullable=False),
        _status("outcome"),
        _timestamp("created_at"),
        _payload(),
        _batch_ref("placements"),
        _job_ref("placements"),
        sa.PrimaryKeyConstraint("placement_id", name=op.f("pk_placements")),
    )
    _indexes("placements", "batch_id", "job_id", "outcome")


def upgrade() -> None:
    """Create every table of the app."""
    _create_batches_and_runs()
    _create_items_and_objects()
    _create_jobs()


def downgrade() -> None:
    """Drop every table (never run by the app: a newer database is refused instead)."""
    for table in (
        "placements",
        "extracted_subtitles",
        "jobs",
        "audio_objects",
        "items",
        "runs",
        "probe_cache",
        "batches",
    ):
        op.drop_table(table)
