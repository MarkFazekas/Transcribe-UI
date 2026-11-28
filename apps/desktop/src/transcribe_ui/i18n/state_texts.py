"""Catalog entries of the states the jobs pages show (architecture §3.1 "Language of the texts").

Batch phases, stages (the steps of the guided flow) and labels (`BatchSummary.labels`), item
phases, job statuses and job kinds. A frontend renders them generically from these entries, so
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
        "manifest_held": tr("Uploaded; waits until the running transcription ends"),
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
