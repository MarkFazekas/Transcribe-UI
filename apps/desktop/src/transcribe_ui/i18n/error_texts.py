"""Catalog entries of the error codes (`ErrorBody.code`, and a failed credential check's code).

One entry per error code of the backend and of the frontends' own error responses; a code whose
wording depends on one detail also has variants (`ERROR_VARIANTS`). The GUI calls jobs what the
backend calls batches, and transcriptions what it calls jobs.
"""

from transcribe_ui.i18n.catalog import tr
from transcribe_ui.i18n.codes import CodeTable


def _local_errors() -> CodeTable:
    """Errors of the request, the local state, the scan, the media tools and the jobs."""
    return {
        "invalid_request": tr("The request is not valid."),
        "cancelled": tr("Stopped. The work done so far is saved; continue any time."),
        "internal_error": tr("Something unexpected went wrong; the details are in the log."),
        "display_unavailable": tr(
            "No display is available for the window. Use the transcribe-ui command line instead."
        ),
        "request_unreadable": tr("The request file cannot be read: {reason}."),
        "invalid_arguments": tr("The command line is not valid."),
        "app_busy": tr("Transcribe-UI is already running in another window or process; close it and try again."),
        "database_too_new": tr("The job database was written by a newer version of Transcribe-UI; update the app."),
        "state_unavailable": tr("The job database cannot be opened; the details are in the log."),
        "state_busy": tr("The job database is busy with another task; try again."),
        "batch_busy": tr("Another task is working on this job; try again when it has finished."),
        "runs_busy": tr("Another task is working on the cloud runs; try again to continue."),
        "phase_conflict": tr("The job has moved on in the meantime; nothing was changed. Reload it and try again."),
        "batch_not_found": tr("The job {batch_id} does not exist."),
        "settings_invalid": tr("The settings file cannot be read or is not valid; save the settings to replace it."),
        "settings_unwritable": tr("The settings cannot be saved; the details are in the log."),
        "root_not_absolute": tr("The folder to scan must be an absolute path."),
        "root_unreadable": tr("The folder to scan cannot be read."),
        "root_not_found": tr("The folder to scan does not exist."),
        "root_not_a_folder": tr("The path to scan is not a folder."),
        "path_outside_root": tr("A selected path is not inside the scanned folder."),
        "media_tool_not_found": tr("{tool} was not found: set its path in the settings or put it on the PATH."),
        "media_tool_not_found.settings": tr(
            "The {tool} path set in the settings is not an absolute path of a file on this computer: {path}",
        ),
        "media_tool_not_found.start": tr("{tool} could not be started: {reason}"),
        "media_tool_failed": tr("{tool} failed on the file."),
        "media_tool_failed.probe": tr("ffprobe could not read the file."),
        "media_tool_failed.convert": tr("ffmpeg could not convert the audio track."),
        "media_tool_failed.extract": tr("ffmpeg could not extract the subtitle stream as SRT."),
        "media_output_unwritable": tr("The converted audio cannot be written or read back; try again."),
        "media_output_unwritable.convert": tr("The converted audio cannot be written: {reason}"),
        "media_index_invalid": tr("The track or stream number must not be negative."),
        "media_tool_timeout": tr("{tool} did not finish within {timeout_s:duration} and was stopped."),
        "wrong_phase": tr("This is not possible at the job's current step."),
        "nothing_selected": tr("The selection contains no media file."),
        "path_not_selected": tr("A file with its own languages is not in the selection."),
        "item_not_found": tr("The job has no such file."),
        "last_item": tr(
            "A job cannot lose its last file; delete the job or clean it up from the cloud instead.",
        ),
        "choice_invalid": tr("A choice does not fit its file."),
        "choice_invalid.item_not_probed": tr("The file was not checked successfully yet; review the job first."),
        "choice_invalid.no_audio": tr("The file has no audio track to transcribe."),
        "choice_invalid.track_missing": tr("Choose the audio track to transcribe."),
        "choice_invalid.track_not_allowed": tr("An audio file is uploaded as it is; it takes no track choice."),
        "choice_invalid.track_out_of_range": tr("The file has no audio track with this number."),
        "choice_invalid.stream_missing": tr("Choose the subtitle stream to extract."),
        "choice_invalid.stream_out_of_range": tr("The file has no subtitle stream with this number."),
        "choice_invalid.stream_not_text": tr(
            "Picture-based subtitles cannot be extracted as text; they can only be transcribed.",
        ),
        "choice_invalid.item_not_in_review": tr("The file is not in review any more; its choices cannot change."),
        "choice_invalid.upload_started": tr(
            "The file's upload has started; its choices cannot change unless it is removed from the job.",
        ),
        "choice_invalid.no_such_job": tr("The file has no transcription in this language."),
        "choice_invalid.no_such_item": tr("The job has no such file."),
    }


def _cloud_errors() -> CodeTable:
    """Errors of the credentials, R2, Runpod, the upload, the runs, the downloads and the cleanup."""
    return {
        "credentials_missing": tr(
            "Some credentials are not set: {missing}. The keyring cannot be used; set the environment variables "
            "{variables}.",
        ),
        "credentials_missing.available": tr(
            "Some credentials are not set: {missing}. Enter them under Settings, Accounts.",
        ),
        "credentials_invalid": tr("Some credentials are not valid: {invalid}. Enter them again."),
        "keyring_unavailable": tr(
            "This system has no keyring to store credentials in. Set the environment variables {variables} instead.",
        ),
        "keyring_failed": tr(
            "The keyring refused to store the credentials ({reason}). Unlock it and try again, or use the "
            "environment variables.",
        ),
        "r2_auth_failed": tr("R2 refused the credentials: the access key ID or the secret access key is wrong."),
        "r2_access_denied": tr(
            "R2 accepted the credentials, but they are not allowed to do this; check the API token's permissions "
            "and bucket.",
        ),
        "r2_bucket_not_found": tr("The R2 bucket does not exist; check the bucket name and the endpoint URL."),
        "r2_unreachable": tr("R2 cannot be reached; check the network connection and the endpoint URL."),
        "r2_object_missing": tr("The R2 object to copy does not exist."),
        "r2_object_too_large": tr("The file is too large for an R2 upload."),
        "r2_failed": tr("The R2 request failed; the details are in the log."),
        "runpod_key_rejected": tr("Runpod rejected the API key (expired, revoked or mistyped). Enter a valid key."),
        "runpod_key_missing_scope": tr(
            "The Runpod API key lacks the permission {scope}. Create a key with the permissions the app lists.",
        ),
        "cloud_unreachable": tr("Runpod cannot be reached or answered unexpectedly. Try again later."),
        "cloud_unreachable.runpod": tr(
            "Whether a cloud run is active cannot be checked, so nothing was started or published; try again later.",
        ),
        "cloud_unreachable.r2": tr(
            "Whether a cloud run is active cannot be checked, so nothing was started or published; try again later.",
        ),
        "cloud_setup_interrupted": tr("The cloud setup was interrupted at {name}. Run it again in a minute."),
        "cloud_setup_interrupted.may_exist": tr(
            "Creating {name} on Runpod failed after the request was sent, so it may exist already. Run the cloud "
            "setup again in a minute; it takes over what was created.",
        ),
        "cloud_setup_interrupted.not_listed": tr(
            "Runpod reports that {name} exists but does not list it yet. Run the cloud setup again in a minute.",
        ),
        "cloud_volume_duplicated": tr(
            "Runpod holds {count} Global Volumes named {name}. They may hold result backups, so the app deletes "
            "none of them: delete the ones you do not need in the Runpod console (Storage page), then run the "
            "cloud setup again.",
        ),
        "cloud_object_in_use": tr(
            "A duplicate of {name} ({object_id}) is in use and cannot be removed. Terminate the cloud machines "
            "using it, then run the cloud setup again.",
        ),
        "cloud_not_set_up": tr("The cloud setup is incomplete. Run the cloud setup first."),
        "cloud_setup_outdated": tr("The cloud setup is outdated. Run the cloud setup again."),
        "upload_incomplete": tr("The job's upload is not complete yet; finish the upload first."),
        "nothing_to_transcribe": tr("The job has nothing to transcribe in the cloud."),
        "batch_id_taken": tr(
            "The cloud already holds data under this job's ID (another computer's job, or one of an earlier "
            "installation); delete this job and create it again, which gives it a new ID.",
        ),
        "review_incomplete": tr("The review is not complete: resolve or remove every file first."),
        "upload_unverified": tr("An uploaded audio file could not be verified in the cloud; upload again."),
        "audio_unusable": tr(
            "This file's audio cannot be uploaded (it is empty, or its extension cannot be used); remove it from "
            "the job.",
        ),
        "source_unreadable": tr(
            "A file of the job cannot be read right now (is its drive or network share connected?); try again "
            "once it can.",
        ),
        "copy_source_missing": tr("A retried file has no audio to copy for one of its transcriptions."),
        "run_active": tr("A cloud run is active; nothing was changed. Try again once it has ended."),
        "nothing_to_launch": tr("There is nothing to transcribe, so no cloud machine was started."),
        "launch_refused": tr(
            "Runpod did not create the cloud machine; nothing was started. Check the Runpod account, then try again.",
        ),
        "run_not_found": tr("The cloud run {run_id} does not exist."),
        "run_not_active": tr("The cloud run has ended already; there is nothing to stop."),
        "launch_unresolved": tr(
            "Whether Runpod created this run's cloud machine is not known yet, so it cannot be stopped; check the "
            "cloud machines in a minute and try again.",
        ),
        "stop_unverified": tr(
            "The cloud machine may still be running: Runpod does not show it stopped yet. Try again."
        ),
        "pod_not_found": tr("Runpod does not know this cloud machine."),
        "not_an_app_pod": tr("This is not one of the app's cloud machines; manage it in the Runpod console."),
        "cleanup_pending": tr(
            "A cloud cleanup was interrupted; repeat the cleanup of these jobs before starting a cloud machine: "
            "{batch_ids}.",
        ),
        "download_plan_invalid": tr("The placement plan has errors; fix them and try again."),
        "custom_plan_unreadable": tr("The custom plan file cannot be read ({reason})."),
        "custom_plan_unwritable": tr("The custom plan file cannot be written ({reason})."),
        "audio_deleted": tr(
            "The job's audio was deleted from the cloud, so it cannot be retried; create a new job instead.",
        ),
        "retry_job_invalid": tr("Only transcriptions that failed or found no speech can be retried."),
        "retry_copying": tr(
            "A retry of this job is still copying its audio; finish or clean up the retry job first.",
        ),
        "results_pending": tr(
            "Some files of the job have no result yet, so their audio is still needed; wait for the "
            "transcription, or delete everything instead.",
        ),
        "manifest_unreadable": tr(
            "This job's description in the cloud cannot be read (another app version, or damaged), so nothing "
            "was deleted.",
        ),
    }


def error_texts() -> CodeTable:
    """Return the entries of every error code in the active language."""
    return {**_local_errors(), **_cloud_errors()}
