"""Stored run progress.

The run payload keeps what the launch knew of the run's work (`weighted_known_duration_s`,
`unknown_runtime_s`), the first report that showed the worker transcribing
(`transcribing_since`) and the progress the last refresh computed (`progress_fraction`,
`remaining_estimate_s`, `estimated_finish_at`); the job payload keeps the run that wrote its
result and when the worker worked on it (`result_run_id`, `result_started_at`,
`result_finished_at`). Rows stored before have none of them: the keys are added as null, so every
stored payload has the shape the app writes, and such a run's progress stays unknown
(architecture §3.7 "Progress fields", §3.8).

Revision ID: 609bed4e68d4
Revises: c41f8e2a6d93
"""

from alembic import op

# Revision identifiers, used by Alembic.
revision = "609bed4e68d4"
down_revision = "c41f8e2a6d93"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the new run and job payload keys as null to every stored row (keys already there are kept)."""
    op.execute(
        "UPDATE runs SET payload = json_insert(payload, '$.weighted_known_duration_s', NULL, "
        "'$.unknown_runtime_s', NULL, '$.transcribing_since', NULL, '$.progress_fraction', NULL, "
        "'$.remaining_estimate_s', NULL, '$.estimated_finish_at', NULL)",
    )
    op.execute(
        "UPDATE jobs SET payload = json_insert(payload, '$.result_run_id', NULL, "
        "'$.result_started_at', NULL, '$.result_finished_at', NULL)",
    )


def downgrade() -> None:
    """Remove the keys again (never run by the app: a newer database is refused instead)."""
    op.execute(
        "UPDATE runs SET payload = json_remove(payload, '$.weighted_known_duration_s', "
        "'$.unknown_runtime_s', '$.transcribing_since', '$.progress_fraction', "
        "'$.remaining_estimate_s', '$.estimated_finish_at')",
    )
    op.execute(
        "UPDATE jobs SET payload = json_remove(payload, '$.result_run_id', "
        "'$.result_started_at', '$.result_finished_at')",
    )
