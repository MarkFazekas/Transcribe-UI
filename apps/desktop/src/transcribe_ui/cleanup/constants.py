"""Constants of retry batches and cloud cleanup (architecture §3.3)."""

from typing import Final

# Batch phases a retry batch can be made from: the source's results are in (failed and no-speech flags).
RETRY_SOURCE_PHASES: Final = frozenset(("transcribed", "downloaded"))
# Job statuses a retry takes: the job failed or found no speech.
FLAGGED_STATUSES: Final = frozenset(("failed", "no_speech"))
# Transcription mode of every retry job (slower, more careful).
RETRY_MODE: Final = "careful"
# Batch phase of a batch whose cloud data is gone; a retry batch in it no longer stands for its jobs.
CLEANED: Final = "cleaned"
# JSON path of a retry batch's source batch id in its batch payload (its `retry_of`).
RETRY_SOURCE_PATH: Final = "$.retry_of.source_batch_id"
# How many batch ids a retry tries when R2 already holds data under them.
RETRY_ID_ATTEMPTS: Final = 10

# Batch phases from which the `audio` scope deletes a batch's audio (the results stay).
AUDIO_PHASES: Final = frozenset(("ready", "transcribed", "downloaded"))
# Batch phases from which the `everything` scope deletes all of a batch's cloud data (`cleaned`: again).
EVERYTHING_PHASES: Final = frozenset(("uploading", "ready", "transcribed", "downloaded", CLEANED))
# Batch phases whose results may not be downloaded yet: `everything` needs a second confirmation.
NOT_DOWNLOADED_PHASES: Final = frozenset(("ready", "transcribed"))
# Request field that confirms a cleanup.
CONFIRM_FIELD: Final = "confirm"
# Request field that confirms deleting results that were not downloaded.
CONFIRM_NOT_DOWNLOADED_FIELD: Final = "confirm_not_downloaded"
# Confirmation code: the batch's audio is deleted from the cloud (`scope` audio).
CONFIRMATION_CLEANUP_AUDIO: Final = "cleanup_audio"
# Confirmation code: all of the batch's cloud data is deleted (`scope` everything).
CONFIRMATION_CLEANUP_EVERYTHING: Final = "cleanup_everything"
# Confirmation code: the subtitles deleted may not have been downloaded yet.
CONFIRMATION_NOT_DOWNLOADED: Final = "cleanup_not_downloaded"
# Confirmation code: all cloud data of a batch this computer does not know is deleted.
CONFIRMATION_UNKNOWN_BATCH: Final = "cleanup_unknown_batch"
# The warning a deletion of another computer's job adds (architecture §3.9 "Downloading twice").
UPLOADED_ELSEWHERE: Final = "Another computer uploaded this batch; it may still need to download the subtitles."
# Confirmation code: all of a batch's cloud data is deleted, and another computer uploaded it (or
# its manifest does not say who did), so that computer may still need its subtitles (architecture §3.9; a
# warning, never a refusal).
CONFIRMATION_CLEANUP_ELSEWHERE: Final = "cleanup_everything_uploaded_elsewhere"
# Confirmation code: a batch this computer does not know, uploaded by another computer (or by an
# unknown one), is deleted from the cloud (architecture §3.9; the stronger form of `cleanup_unknown_batch`).
CONFIRMATION_UNKNOWN_ELSEWHERE: Final = "cleanup_unknown_batch_uploaded_elsewhere"
# Progress stage of `cleanup_cloud`: objects deleted so far.
CLEANUP_STAGE: Final = "delete_objects"

# Error code: the source batch's audio was cleaned up, so it cannot be retried.
ERROR_AUDIO_DELETED: Final = "audio_deleted"
# Error code: a job of `create_retry_batch` is not a failed or no-speech transcribe job of the source.
ERROR_RETRY_JOB_INVALID: Final = "retry_job_invalid"
# Reason of `retry_job_invalid`: the source has no transcribe job with this item and language.
REASON_NO_SUCH_JOB: Final = "no_such_job"
# Reason of `retry_job_invalid`: the job neither failed nor found no speech.
REASON_NOT_FLAGGED: Final = "not_flagged"
# Error code: a retry batch of this batch is still copying its audio, so the audio must stay.
ERROR_RETRY_COPYING: Final = "retry_copying"
# Error code: the batch has jobs without a result; deleting the audio would fail them.
ERROR_RESULTS_PENDING: Final = "results_pending"
# Error code: another computer removed the batch from the cloud; nothing of it is left there to delete.
ERROR_REMOVED_FROM_CLOUD: Final = "batch_removed_from_cloud"
# Error code: the manifest of a batch this database does not know cannot be read, so nothing is deleted.
ERROR_MANIFEST_UNREADABLE: Final = "manifest_unreadable"
