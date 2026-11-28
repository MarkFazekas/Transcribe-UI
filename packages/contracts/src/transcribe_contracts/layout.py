"""Object keys of the R2 layout (architecture §4).

Every key is built and parsed here, so the desktop app and the worker can never disagree on a
path. Builders refuse key segments that could escape their folder (path separators, a leading
dot).
"""

import re
from typing import NamedTuple

from transcribe_contracts.constants import (
    AUDIO_FOLDER,
    BATCH_ID_PATTERN,
    BATCH_METADATA_PREFIX,
    DONE_OBJECT_NAME,
    JSON_SUFFIX,
    KEY_SEPARATORS,
    RESULTS_FOLDER,
    RUN_LEASE_OBJECT_NAME,
    RUN_LOG_OBJECT_NAME,
    RUN_STATUS_OBJECT_NAME,
    RUNS_PREFIX,
    SRT_SUFFIX,
    TRACK_MARKER,
    ULID_PATTERN,
)


class ResultKey(NamedTuple):
    """Parts of a result key `<batch_id>/results/<item_id>.<language><suffix>`."""

    batch_id: str
    item_id: str
    language: str
    suffix: str


def manifest_key(batch_id: str) -> str:
    """Return the key of a batch manifest."""
    return f"{BATCH_METADATA_PREFIX}{_segment(batch_id)}{JSON_SUFFIX}"


def batch_id_from_manifest_key(key: str) -> str | None:
    """Return the batch id encoded in a manifest key, or None if the key is not a manifest."""
    if not key.startswith(BATCH_METADATA_PREFIX) or not key.endswith(JSON_SUFFIX):
        return None
    batch_id = key.removeprefix(BATCH_METADATA_PREFIX).removesuffix(JSON_SUFFIX)
    if re.fullmatch(BATCH_ID_PATTERN, batch_id) is None:
        return None
    return batch_id


def batch_prefix(batch_id: str) -> str:
    """Return the prefix under which every object of a batch lives (except its manifest)."""
    return f"{_segment(batch_id)}/"


def audio_prefix(batch_id: str, item_id: str) -> str:
    """Return the prefix shared by every audio object of one item."""
    return f"{batch_prefix(batch_id)}{AUDIO_FOLDER}/{_segment(item_id)}."


def audio_key(batch_id: str, item_id: str, extension: str, track_index: int | None) -> str:
    """Return the key of an audio object.

    Args:
        batch_id: The batch the item belongs to.
        item_id: The item's ULID.
        extension: File extension without the dot.
        track_index: Audio track of a video file, or None for an audio file uploaded as-is.
    """
    track_part = "" if track_index is None else f"{TRACK_MARKER}{track_index}."
    return f"{audio_prefix(batch_id, item_id)}{track_part}{_segment(extension)}"


def result_srt_key(batch_id: str, item_id: str, language: str) -> str:
    """Return the key of a job's subtitle."""
    return f"{_result_base(batch_id, item_id, language)}{SRT_SUFFIX}"


def result_json_key(batch_id: str, item_id: str, language: str) -> str:
    """Return the key of a job's result (the job's "done" marker)."""
    return f"{_result_base(batch_id, item_id, language)}{JSON_SUFFIX}"


def results_prefix(batch_id: str) -> str:
    """Return the prefix of every subtitle and result of a batch."""
    return f"{batch_prefix(batch_id)}{RESULTS_FOLDER}/"


def parse_result_key(key: str) -> ResultKey | None:
    """Split a result key into its parts, or return None if it is not a result key."""
    batch_id, _, rest = key.partition(f"/{RESULTS_FOLDER}/")
    if re.fullmatch(BATCH_ID_PATTERN, batch_id) is None or "/" in rest:
        return None
    for suffix in (JSON_SUFFIX, SRT_SUFFIX):
        if rest.endswith(suffix):
            item_id, _, language = rest.removesuffix(suffix).partition(".")
            if item_id and language and "." not in language:
                return ResultKey(batch_id, item_id, language, suffix)
    return None


def done_key(batch_id: str) -> str:
    """Return the key of the batch summary the worker writes after the batch's last job."""
    return f"{batch_prefix(batch_id)}{DONE_OBJECT_NAME}"


def run_status_key(run_id: str) -> str:
    """Return the key of a worker run's heartbeat and progress object."""
    return f"{RUNS_PREFIX}{_segment(run_id)}/{RUN_STATUS_OBJECT_NAME}"


def run_log_key(run_id: str) -> str:
    """Return the key of a worker run's log."""
    return f"{RUNS_PREFIX}{_segment(run_id)}/{RUN_LOG_OBJECT_NAME}"


def run_lease_key(run_id: str) -> str:
    """Return the key of a run's lease (the batches it may process; written by the launching desktop)."""
    return f"{RUNS_PREFIX}{_segment(run_id)}/{RUN_LEASE_OBJECT_NAME}"


def run_id_from_lease_key(key: str) -> str | None:
    """Return the run id encoded in a lease key, or None if the key is not a run lease."""
    suffix = f"/{RUN_LEASE_OBJECT_NAME}"
    if not key.startswith(RUNS_PREFIX) or not key.endswith(suffix):
        return None
    run_id = key.removeprefix(RUNS_PREFIX).removesuffix(suffix)
    if re.fullmatch(ULID_PATTERN, run_id) is None:
        return None
    return run_id


def _result_base(batch_id: str, item_id: str, language: str) -> str:
    name = f"{_segment(item_id)}.{_segment(language)}"
    return f"{results_prefix(batch_id)}{name}"


def _segment(text: str) -> str:
    has_separator = any(separator in text for separator in KEY_SEPARATORS)
    if not text or text.startswith(".") or has_separator:
        msg = f"invalid key segment {text!r}"
        raise ValueError(msg)
    return text
