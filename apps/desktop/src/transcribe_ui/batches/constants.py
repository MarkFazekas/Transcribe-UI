"""Constants of the batches: ids, phases, job states and error codes (architecture §3.3)."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, get_args

from transcribe_ui.cleanup.constants import AUDIO_PHASES, EVERYTHING_PHASES, RETRY_SOURCE_PHASES
from transcribe_ui.media.constants import ERROR_MEDIA_TOOL_FAILED, ERROR_MEDIA_TOOL_TIMEOUT
from transcribe_ui.placement.constants import DOWNLOAD_PHASES
from transcribe_ui.state.models import BatchPhase
from transcribe_ui.upload.constants import UPLOAD_PHASES

# strftime format of a batch id from the local creation time (`batch_YYYYMMDD_HHmmss`).
BATCH_ID_FORMAT: Final = "batch_%Y%m%d_%H%M%S"
# First collision suffix of a batch id (`_2`, then `_3`, ...).
FIRST_BATCH_ID_SUFFIX: Final = 2
# Separator between a batch id and its collision suffix.
BATCH_ID_SUFFIX_SEPARATOR: Final = "_"

# Phase of a batch (and its items) while its selection is edited.
COMPOSING: Final = "composing"
# Phase of a batch (and its items) in the review step.
REVIEWING: Final = "reviewing"
# Every batch phase (unhiding is possible in any of them).
ALL_PHASES: Final[frozenset[str]] = frozenset(get_args(BatchPhase.__value__))
# Batch phases in which the items can still be edited (`edit_batch`).
EDITABLE_PHASES: Final = frozenset(("composing",))
# Batch phases in which a batch can be deleted (nothing is in the cloud yet, Q26).
DELETABLE_PHASES: Final = frozenset(("composing", "reviewing"))
# Batch phases in which items are probed, resolved and excluded (§3.3; `uploading` for items back in review).
REVIEW_PHASES: Final = frozenset(("reviewing", "uploading"))
# Batch phases in which an item can be sent back to review by the upload (its source changed).
UPLOADING_PHASES: Final = frozenset(("uploading",))
# Batch phases in which a batch counts as finished and can be hidden.
FINISHED_PHASES: Final = frozenset(("downloaded", "cleaned"))
# Item phases whose choices can still change (probed or waiting for a probe or a choice).
ITEM_REVIEW_PHASES: Final = frozenset(("reviewing", "reviewed"))

# An allowed action's rule: the batch phases its handler accepts, and the further conditions.
type ActionRule = tuple[frozenset[str], tuple[str, ...]]

# Condition of an allowed action: the batch has more than one item (`exclude_items` keeps one).
CONDITION_SEVERAL_ITEMS: Final = "several_items"
# Condition of an allowed action: every item is reviewed, unless the upload has started (`run_upload`).
CONDITION_REVIEW_COMPLETE: Final = "review_complete"
# Condition of an allowed action: no transcribe job waits for its result (`cleanup_cloud` scope `audio`).
CONDITION_RESULTS_IN: Final = "results_in"
# Condition of an allowed action: a transcribe job failed or found no speech (`create_retry_batch`).
CONDITION_FLAGGED_JOBS: Final = "flagged_jobs"
# Condition of an allowed action: the batch's audio was not cleaned up (`create_retry_batch`).
CONDITION_AUDIO_KEPT: Final = "audio_kept"
# Condition of an allowed action: no retry batch of this batch still copies its audio (`cleanup_cloud`).
CONDITION_NO_COPYING_RETRY: Final = "no_copying_retry"
# The batch requests a batch allows now, in the order the frontends offer them: by action, the
# batch phases the handler accepts and the further local conditions (`batches/actions.py`). It
# restates the handlers' own guards, which stay where they are; a drift test links the two.
# `cleanup_cloud` and `set_batch_hidden` are listed per scope and per direction.
ACTION_RULES: Final[Mapping[str, ActionRule]] = MappingProxyType(
    {
        "edit_batch": (EDITABLE_PHASES, ()),
        "start_review": (frozenset((COMPOSING, REVIEWING)), ()),
        "delete_batch": (DELETABLE_PHASES, ()),
        "review_batch": (REVIEW_PHASES, ()),
        "resolve_review": (REVIEW_PHASES, ()),
        "exclude_items": (REVIEW_PHASES, (CONDITION_SEVERAL_ITEMS,)),
        "run_upload": (UPLOAD_PHASES, (CONDITION_REVIEW_COMPLETE,)),
        "plan_download": (DOWNLOAD_PHASES, ()),
        "run_download": (DOWNLOAD_PHASES, ()),
        "export_custom_plan": (DOWNLOAD_PHASES, ()),
        "import_custom_plan": (DOWNLOAD_PHASES, ()),
        "create_retry_batch": (RETRY_SOURCE_PHASES, (CONDITION_AUDIO_KEPT, CONDITION_FLAGGED_JOBS)),
        "cleanup_audio": (AUDIO_PHASES, (CONDITION_NO_COPYING_RETRY, CONDITION_RESULTS_IN)),
        "cleanup_everything": (EVERYTHING_PHASES, (CONDITION_NO_COPYING_RETRY,)),
        "hide_batch": (FINISHED_PHASES, ()),
        "show_batch": (ALL_PHASES, ()),
    },
)

# Status of a job that has no result yet (later parts add the others).
JOB_PENDING: Final = "pending"
# Mode of a new job; `careful` is only used by retry batches.
DEFAULT_MODE: Final = "normal"

# Progress stage of `review_batch` while it probes the files.
PROBE_STAGE: Final = "probe"
# Progress stage of `exclude_items` while it deletes the uploaded objects of the excluded items.
DELETE_OBJECTS_STAGE: Final = "delete_objects"
# Request field that confirms `delete_batch` and `exclude_items`.
CONFIRM_FIELD: Final = "confirm"
# Separator of the relative paths in confirmation details (the same on every OS).
RELATIVE_SEPARATOR: Final = "/"

# Error code: the request is not possible in the batch's current phase; nothing was changed.
ERROR_WRONG_PHASE: Final = "wrong_phase"
# Error code: the selection of `create_batch` or `edit_batch` selects no media file.
ERROR_NOTHING_SELECTED: Final = "nothing_selected"
# Error code: an `item_languages` entry names a file the selection does not select.
ERROR_PATH_NOT_SELECTED: Final = "path_not_selected"
# Error code: an item id of the request is not an item of the batch.
ERROR_ITEM_NOT_FOUND: Final = "item_not_found"
# Error code: `exclude_items` would leave the batch without items.
ERROR_LAST_ITEM: Final = "last_item"
# Error code: a choice of `resolve_review` cannot be applied; nothing was changed.
ERROR_CHOICE_INVALID: Final = "choice_invalid"

# Choice reason: the item has no probe result (or a problem) yet; send `review_batch` first.
REASON_NOT_PROBED: Final = "item_not_probed"
# Choice reason: the file has no audio track to transcribe.
REASON_NO_AUDIO: Final = "no_audio"
# Choice reason: a video file needs an audio track to transcribe.
REASON_TRACK_MISSING: Final = "track_missing"
# Choice reason: an audio file is uploaded as it is, so it takes no track.
REASON_TRACK_NOT_ALLOWED: Final = "track_not_allowed"
# Choice reason: the file has no audio track with this index.
REASON_TRACK_OUT_OF_RANGE: Final = "track_out_of_range"
# Choice reason: an extraction needs a subtitle stream.
REASON_STREAM_MISSING: Final = "stream_missing"
# Choice reason: the file has no subtitle stream with this index.
REASON_STREAM_OUT_OF_RANGE: Final = "stream_out_of_range"
# Choice reason: picture-based subtitles (PGS, VobSub, ...) cannot be extracted as text, only transcribed.
REASON_STREAM_NOT_TEXT: Final = "stream_not_text"
# Choice reason: the item is not in review (already uploaded).
REASON_NOT_IN_REVIEW: Final = "item_not_in_review"
# Choice reason: in an `uploading` batch, the item's upload has started (it has audio or extraction rows).
REASON_UPLOAD_STARTED: Final = "upload_started"
# Choice reason: the item has no job in this language.
REASON_NO_SUCH_JOB: Final = "no_such_job"
# Choice reason: the batch has no item with this id.
REASON_NO_SUCH_ITEM: Final = "no_such_item"
# Message of `choice_invalid` per reason.
CHOICE_MESSAGES: Final[Mapping[str, str]] = MappingProxyType(
    {
        REASON_NOT_PROBED: "The file was not probed successfully yet; send review_batch first.",
        REASON_NO_AUDIO: "The file has no audio track to transcribe.",
        REASON_TRACK_MISSING: "Choose the audio track to transcribe.",
        REASON_TRACK_NOT_ALLOWED: "An audio file is uploaded as it is; it takes no track choice.",
        REASON_TRACK_OUT_OF_RANGE: "The file has no audio track with this index.",
        REASON_STREAM_MISSING: "Choose the subtitle stream to extract.",
        REASON_STREAM_OUT_OF_RANGE: "The file has no subtitle stream with this index.",
        REASON_STREAM_NOT_TEXT: "Picture-based subtitles cannot be extracted as text; they can only be transcribed.",
        REASON_NOT_IN_REVIEW: "The file is not in review any more; its choices cannot change.",
        REASON_UPLOAD_STARTED: "The file's upload has started; its choices cannot change unless it is excluded.",
        REASON_NO_SUCH_JOB: "The file has no job in this language.",
        REASON_NO_SUCH_ITEM: "The batch has no item with this id.",
    },
)

# Item problem: the source file is gone or cannot be read.
PROBLEM_SOURCE_MISSING: Final = "source_missing"
# Media error codes that make a probe an item problem instead of failing the whole review.
PROBE_FAILURE_CODES: Final = frozenset((ERROR_MEDIA_TOOL_FAILED, ERROR_MEDIA_TOOL_TIMEOUT))
# Item problem: ffprobe failed or timed out on the file.
PROBLEM_PROBE_FAILED: Final = "probe_failed"
# Item problem: the file kept changing while it was probed (size or mtime), so the probe is not trusted.
PROBLEM_SOURCE_CHANGING: Final = "source_changing"
# Message of `source_changing`.
SOURCE_CHANGING_MESSAGE: Final = "The file changed while it was probed; review the batch again once it is complete."
# How often a file that changed during its probe is probed before it is reported as `source_changing`.
PROBE_ATTEMPTS: Final = 2
# Message of `source_missing`.
SOURCE_MISSING_MESSAGE: Final = "The file is gone or cannot be read; restore it, or exclude it from the batch."
