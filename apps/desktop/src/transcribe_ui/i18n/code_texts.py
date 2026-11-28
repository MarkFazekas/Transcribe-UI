"""Catalog entries of the backend's codes other than errors (architecture §3.1 "Language of the texts").

Confirmations (`Confirmation.code`), placement plan errors and reasons, download failures,
credential checks that passed, the desktop's job failures (`result_error_code`), scan warnings,
item problems, the cost estimate's price source and the progress stages (the states of batches,
items and jobs: `i18n/state_texts.py`). The GUI calls jobs what the backend calls batches, and
transcriptions what it calls jobs.
"""

from transcribe_ui.i18n.catalog import ntr, tr
from transcribe_ui.i18n.codes import CodeTable, Details, count_of
from transcribe_ui.runs.constants import WAITING_RUN_STAGES


def _delete_batch(details: Details) -> str:
    return ntr(
        "The job and its {items} file will be deleted from this computer's job list.",
        "The job and its {items} files will be deleted from this computer's job list.",
        count_of(details, "items"),
    )


def _exclude_items(details: Details) -> str:
    count = count_of(details, "items")
    if details.get("deletes_uploaded_audio") is True:
        return ntr(
            "{items} file will be removed from the job. Its audio already uploaded to the cloud is deleted.",
            "{items} files will be removed from the job. Their audio already uploaded to the cloud is deleted.",
            count,
        )
    return ntr("{items} file will be removed from the job.", "{items} files will be removed from the job.", count)


def _existing_languages(details: Details) -> str:
    return ntr(
        "{count} selected file already has a subtitle in a target language.",
        "{count} selected files already have a subtitle in a target language.",
        count_of(details, "count"),
    )


def _launch_plan(details: Details) -> str:
    parts = []
    if details.get("changed") is True:
        parts.append(tr("The plan changed since it was confirmed (the work, the time limit or the test mode)."))
    parts.append(
        ntr(
            "Start a cloud machine for {jobs} transcription ({audio_s:duration} of audio); estimated cost "
            "{cost_low_usd:.2f}-{cost_high_usd:.2f} USD.",
            "Start a cloud machine for {jobs} transcriptions ({audio_s:duration} of audio); estimated cost "
            "{cost_low_usd:.2f}-{cost_high_usd:.2f} USD.",
            count_of(details, "jobs"),
        ),
    )
    unknown = count_of(details, "unknown_batches")
    if unknown:
        parts.append(
            ntr(
                "{unknown_batches} of the jobs was uploaded from another computer.",
                "{unknown_batches} of the jobs were uploaded from another computer.",
                unknown,
            ),
        )
    return " ".join(parts)


def _adopt_batch(details: Details) -> str:
    return ntr(
        "Add the cloud job {batch_id} ({files} file), uploaded from another computer, to this computer's jobs?",
        "Add the cloud job {batch_id} ({files} files), uploaded from another computer, to this computer's jobs?",
        count_of(details, "files"),
    )


def _unknown_batch(details: Details) -> str:
    return ntr(
        "The job {batch_id} is not known on this computer; all its data ({files} file) will be deleted from the cloud.",
        "The job {batch_id} is not known on this computer; all its data ({files} files) will be deleted from "
        "the cloud.",
        count_of(details, "files"),
    )


def _overwrite_targets(details: Details) -> str:
    return ntr(
        "{count} existing subtitle file will be replaced.",
        "{count} existing subtitle files will be replaced.",
        count_of(details, "count"),
    )


def _cancel_worker(details: Details) -> str:
    if details.get("stage") in WAITING_RUN_STAGES:
        return tr(
            "Stop waiting for a cloud machine? Nothing has been transcribed yet; the files wait for the next start "
            "of transcription.",
        )
    return tr(
        "Stop the cloud machine now? Subtitles already finished are kept; the files not done yet wait for the "
        "next start of transcription.",
    )


def confirmation_texts() -> CodeTable:
    """Return the entries of every confirmation code."""
    return {
        "delete_batch": _delete_batch,
        "exclude_items": _exclude_items,
        "existing_languages": _existing_languages,
        "launch_plan": _launch_plan,
        "cleanup_audio": tr(
            "The job's audio will be deleted from the cloud; its subtitles stay, but it can no longer be retried.",
        ),
        "cleanup_everything": tr(
            "All of the job's data will be deleted from the cloud: audio, subtitles and its description.",
        ),
        "cleanup_not_downloaded": tr(
            "The job's subtitles may not be downloaded yet; after this, only the backup on the cloud volume "
            "keeps them.",
        ),
        "cleanup_unknown_batch": _unknown_batch,
        "adopt_cloud_batch": _adopt_batch,
        "overwrite_targets": _overwrite_targets,
        "terminate_pod": tr(
            "Terminate the cloud machine {name} ({status})? Its work stops at once; finished results stay in "
            "the cloud.",
        ),
        "cancel_worker": _cancel_worker,
        "cancel_queued_pod": tr(
            "Stop waiting for the queued cloud machine {name} ({status})? A machine it has started meanwhile is "
            "stopped too; finished results stay in the cloud.",
        ),
    }


def plan_error_texts() -> CodeTable:
    """Return the entries of every placement plan error code."""
    return {
        "name_too_long": tr(
            "{file} ({language}): the subtitle name {name} is too long for the target (with the tail of its "
            "temporary file, at most {max_length} characters).",
        ),
        "path_too_long": tr("{file} ({language}): the target path is longer than Windows allows."),
        "duplicate_target": tr("{file} ({language}): another subtitle has the same target."),
        "target_unreadable": tr("{file} ({language}): the target cannot be checked (no permission)."),
        "scan_root_invalid": tr("The scanned folder is not an absolute path or contains '..'."),
        "source_path_invalid": tr("{file} ({language}): the file's path is not absolute or contains '..'."),
        "target_not_a_file": tr("{file} ({language}): the target exists and is not a file."),
        "outside_scan_root": tr("{file} ({language}): the file is not inside the scanned folder."),
        "output_not_absolute": tr("The output folder is not an absolute path."),
        "no_local_sources": tr(
            "The job was uploaded from another computer, so its media files are not on this one; choose another "
            "placement.",
        ),
        "custom_plan_schema": tr("The custom plan does not match its format: {problems}"),
        "custom_plan_duplicate_job": tr("{file} ({language}) is listed more than once in the plan."),
        "custom_plan_unknown_job": tr("The plan lists {item_id} ({language}), which is not part of this download."),
        "custom_plan_missing_job": tr("{file} ({language}) is missing from the plan."),
        "custom_plan_source_changed": tr(
            "{file} ({language}): source_path or original_folder was changed in the plan.",
        ),
        "custom_plan_invalid_name": tr("{file} ({language}): the name {name} cannot be used on the target."),
        "custom_plan_invalid_name.not_a_name": tr(
            "{file} ({language}): the plan gives no file or folder name ({name})."
        ),
        "custom_plan_invalid_name.alternate_data_stream": tr(
            "{file} ({language}): the name {name} contains ':' (Windows reads it as an alternate data stream).",
        ),
        "custom_plan_invalid_name.forbidden_characters": tr(
            "{file} ({language}): the name {name} contains characters a name cannot hold: {characters}",
        ),
        "custom_plan_invalid_name.too_long": tr(
            "{file} ({language}): the name {name} is too long (the limit is {max_length}).",
        ),
        "custom_plan_invalid_name.trailing_dot_or_space": tr(
            "{file} ({language}): the name {name} ends with a dot or a space.",
        ),
        "custom_plan_invalid_name.reserved_device_name": tr(
            "{file} ({language}): {name} is a name Windows reserves for a device.",
        ),
        "custom_plan_not_srt": tr("{file} ({language}): output_file_name does not end with .srt."),
        "custom_plan_outside_root": tr("{file} ({language}): the target is outside the output folder."),
    }


def download_failure_texts() -> CodeTable:
    """Return the entries of every download failure code."""
    return {
        "subtitle_missing": tr("{file} ({language}): the subtitle is not in the cloud any more."),
        "download_write_failed": tr(
            "{file} ({language}): the subtitle cannot be written; try again once the target is writable.",
        ),
    }


def rename_reason_texts() -> CodeTable:
    """Return the entries of every reason a target name differs from the default name."""
    return {
        "folders": tr("Folder names were added to tell apart files of different folders."),
        "source_extension": tr("The media file's extension was added to tell apart files of the same folder."),
    }


def check_texts() -> CodeTable:
    """Return the entries of the credential checks that passed (a failed check has an error code)."""
    return {
        "r2_check_passed": tr("R2 accepted the credentials and the bucket can be listed."),
        "runpod_check_passed": tr("Runpod accepted the API key and the cloud machines can be listed."),
    }


def result_error_texts() -> CodeTable:
    """Return the entries of the job failures the desktop decides (`result_error_code`)."""
    return {
        "result_unreadable": tr("The result of the transcription cannot be read."),
        "result_missing": tr("The cloud machine left no result for the transcription."),
    }


def scan_warning_texts() -> CodeTable:
    """Return the entries of every scan warning code."""
    return {
        "invalid_name": tr("The name is not valid Unicode; rename it to include it."),
        "link_skipped": tr("Links are not followed."),
        "unreadable": tr("This could not be read."),
        "too_deep": tr("Folders this deep are not scanned."),
        "no_media_at_path": tr("No media file is at or below this path."),
    }


def item_problem_texts() -> CodeTable:
    """Return the entries of every item problem code."""
    return {
        "source_missing": tr("The file is gone or cannot be read; restore it, or remove it from the job."),
        "probe_failed": tr("The file could not be checked."),
        "source_changing": tr("The file changed while it was checked; review the job again once it is complete."),
    }


def price_source_texts() -> CodeTable:
    """Return the entries of the cost estimate's price sources that need a note."""
    return {
        "fallback": tr("The Runpod price list does not list the GPU; the estimate uses the usual price."),
    }


def stage_texts() -> CodeTable:
    """Return the entries of every progress stage."""
    return {
        "scan": tr("Scanning folders"),
        "probe": tr("Checking files"),
        "upload_item": tr("Uploading files"),
        "upload_bytes": tr("Uploading"),
        "download": tr("Downloading subtitles"),
        "delete_objects": tr("Deleting from the cloud"),
    }
