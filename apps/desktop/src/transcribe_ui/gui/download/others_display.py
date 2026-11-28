"""Display data of the list of other computers' jobs in the cloud (architecture §3.9).

Qt-free. Everything shown comes from `list_cloud_batches`; only presentation is decided here:
the texts of a job's state, the numbers and the audio length.
"""

from dataclasses import dataclass

from transcribe_ui.batches.cloud_responses import CloudBatch
from transcribe_ui.i18n.catalog import tr
from transcribe_ui.i18n.formats import format_duration, format_number


@dataclass(frozen=True)
class CloudJobRow:
    """Another computer's job in the cloud, as the list shows it."""

    batch_id: str
    files: str
    audio: str
    state: str


def _state_text(batch: CloudBatch) -> str:
    if batch.state == "waiting":
        return tr("Waiting for transcription")
    if batch.state == "transcribing":
        done = format_number(batch.done_jobs)
        return tr("Transcribing: {done} of {total} done").format(done=done, total=format_number(batch.jobs))
    return tr("Transcribed")


def cloud_job_row(batch: CloudBatch) -> CloudJobRow:
    """Return how another computer's job is listed."""
    return CloudJobRow(
        batch_id=batch.batch_id,
        files=format_number(batch.files),
        audio=format_duration(batch.audio_s),
        state=_state_text(batch),
    )
