"""Catalog entries of the states the jobs pages show (architecture §3.1 "Language of the texts").

Batch phases, stages (the steps of the guided flow) and labels (`BatchSummary.labels`), item
phases, job statuses and job kinds; and what the Cloud page shows of a run: its stage, why it
ended, and the state its worker reports. A frontend renders them generically from these entries, so
a new code needs only its entry. The GUI calls jobs what the backend calls batches, and
transcriptions what it calls jobs.
"""

from transcribe_ui.i18n.catalog import tr
from transcribe_ui.i18n.codes import CodeTable


def batch_phase_texts() -> CodeTable:
    """Return the entries of every batch phase (shown as the job's status)."""
    return {
        "composing": tr("Selecting files"),
        "reviewing": tr("In review"),
        "uploading": tr("Uploading"),
        "ready": tr("Ready for transcription"),
        "transcribed": tr("Transcribed"),
        "downloaded": tr("Downloaded"),
        "cleaned": tr("Deleted from the cloud"),
    }


def batch_stage_texts() -> CodeTable:
    """Return the entries of every stage of a batch (the steps of the guided flow, and done)."""
    return {
        "files": tr("Files"),
        "review_upload": tr("Review & upload"),
        "transcription": tr("Transcription"),
        "download": tr("Download"),
        "done": tr("Done"),
    }


def batch_label_texts() -> CodeTable:
    """Return the entries of every batch label (`BatchSummary.labels`)."""
    return {
        "cleanup_pending": tr("Cloud cleanup interrupted; repeat it"),
        "upload_unfinished": tr("Upload not finished"),
        "subtitles_ready": tr("Subtitles ready to download"),
        "flagged_jobs": tr("Some transcriptions failed or found no speech"),
        "audio_deleted": tr("Audio deleted from the cloud"),
        "hidden": tr("Hidden"),
    }


def item_phase_texts() -> CodeTable:
    """Return the entries of every item phase (shown as the file's status)."""
    return {
        "composing": tr("Selected"),
        "reviewing": tr("In review"),
        "reviewed": tr("Reviewed"),
        "uploaded": tr("Uploaded"),
    }


def job_status_texts() -> CodeTable:
    """Return the entries of every job status (shown as the transcription's status)."""
    return {
        "pending": tr("Waiting"),
        "extracted": tr("Extracted"),
        "ok": tr("Done"),
        "no_speech": tr("No speech"),
        "failed": tr("Failed"),
    }


def job_kind_texts() -> CodeTable:
    """Return the entries of every job kind."""
    return {
        "transcribe": tr("Transcription"),
        "extract": tr("Embedded subtitle"),
    }


def run_stage_texts() -> CodeTable:
    """Return the entries of every run stage (`RunProgress.stage`)."""
    return {
        "launching": tr("Requesting a cloud machine"),
        "waiting": tr("Waiting for a free cloud machine"),
        "starting": tr("Starting"),
        "transcribing": tr("Transcribing"),
        "stopping": tr("Done; the cloud machine is stopping"),
        "finished": tr("Finished"),
        "failed": tr("Failed"),
        "cancelled": tr("Stopped"),
        "launch_failed": tr("Could not be started"),
    }


def run_end_reason_texts() -> CodeTable:
    """Return the entries of every reason a run ended (`RunProgress.end_reason`)."""
    return {
        "worker_finished": tr("The cloud machine finished its work."),
        "worker_failed": tr("The cloud machine reported an error."),
        "pod_gone": tr("The cloud machine stopped without a final report."),
        "deadline": tr("The app stopped the cloud machine at its time limit."),
        "queue_ended": tr("No cloud machine became free in time."),
        "cancelled": tr("Stopped by the user."),
        "launch_failed": tr("Runpod did not start the cloud machine."),
    }


def worker_state_texts() -> CodeTable:
    """Return the entries of every state the worker reports (`WorkerProgress.state`)."""
    return {
        "starting": tr("Loading"),
        "transcribing": tr("Transcribing"),
        "finished": tr("Finished"),
        "failed": tr("Failed"),
        "unreadable": tr("Report unreadable"),
    }
