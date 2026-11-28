"""The `cli-probe` check of the desktop smoke test (docs/ai/plans/m8.md §1 "Smoke tests").

In a folder of its own, a second of audio written with the standard library's `wave` is scanned,
put in a batch and reviewed through `<cli> run -` with only the OS's own folders on `PATH`.
`review_batch` is the first request that probes: without an ffprobe it answers
`media_tool_not_found`, and a probe that fails leaves the file with a problem. So `review_batch`
answering `ok` and `get_batch` showing the WAV reviewed without a problem prove that the bundled
ffprobe was found through `sys._MEIPASS` and read the file. (No answer before the upload carries
an audio file's duration or tracks; the review views list only videos and files with subtitles.) Used
by `scripts/smoke_desktop_build.py` on the runner and by `scripts/smoke_desktop_floor.py` in the
ubuntu:22.04 container.
"""

import os
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path

from smoke_desktop_checks import Answer, JsonObject, Outcome, Smoke, answered_ok, failed, judged, respond
from smoke_desktop_process import path_without_tools

# Name of the check.
PROBE_CHECK = "cli-probe"
# The WAV the check writes: its name, sample rate, length in seconds and sample width in bytes (mono).
PROBE_FILE = "tone.wav"
PROBE_RATE_HZ = 16000
PROBE_SECONDS = 1
PROBE_SAMPLE_BYTES = 2
# Phase and problem of the WAV's item once ffprobe has read it.
PROBED = ("reviewed", None)
# The batch actions after `create_batch`, in order: the review starts, probes, and the batch is read.
PROBE_ACTIONS = ("start_review", "review_batch", "get_batch")


@dataclass(frozen=True)
class ProbeSetup:
    """Where the check writes its WAV, where the executable sees that folder and the minimal `PATH` it gets."""

    local: Path
    seen: str
    path: str


def minimal_path() -> str:
    """Return a `PATH` of the OS's own folders only, without one that holds ffmpeg or ffprobe.

    So neither a media tool of the machine nor a DLL next to one can be found.
    """
    if sys.platform == "win32":
        root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        return path_without_tools(f"{root}\\System32;{root}")
    return path_without_tools("/usr/bin:/bin")


def write_probe_wav(path: Path) -> None:
    """Write a second of silence as a 16-bit mono WAV with the standard library."""
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(PROBE_SAMPLE_BYTES)
        audio.setframerate(PROBE_RATE_HZ)
        audio.writeframes(bytes(PROBE_RATE_HZ * PROBE_SECONDS * PROBE_SAMPLE_BYTES))


def _create_batch(folder: str) -> JsonObject:
    include = {"operation": "include", "path": folder, "filters": {}}
    scan = {"root": folder}
    return {"action": "create_batch", "scan": scan, "operations": [include], "languages": ["en"]}


def _field(document: object, *keys: str) -> object:
    """Return the value at `keys` in nested JSON objects, or None."""
    for key in keys:
        if not isinstance(document, dict):
            return None
        document = document.get(key)
    return document


def probed_item(batch: object) -> tuple[object, object]:
    """Return phase and problem of the WAV's item in a `get_batch` answer (None, None when it is missing)."""
    listed = _field(batch, "batch", "items")
    entries = listed if isinstance(listed, list) else []
    found = [entry for entry in entries if _field(entry, "name") == PROBE_FILE]
    if not found:
        return None, None
    phase = _field(found[0], "phase")
    return phase, _field(found[0], "problem")


def _batch_actions(smoke: Smoke, setup: ProbeSetup, batch_id: object) -> Answer:
    """Run the batch actions after `create_batch`; return the last answer, or the first that was not ok."""
    answer: Answer | None = None
    path_only = {"PATH": setup.path}
    for action in PROBE_ACTIONS:
        answer = respond(smoke, {"action": action, "batch_id": batch_id}, path_only)
        if not answered_ok(answer):
            return answer
    if answer is None:
        msg = "no batch action to run"
        raise ValueError(msg)
    return answer


def check_cli_probe(smoke: Smoke, setup: ProbeSetup) -> Outcome:
    """A WAV is scanned, put in a batch and reviewed with only the OS's folders on `PATH`.

    The review probes the file, so its answer shows the WAV's track only when the bundled
    ffprobe was found through `sys._MEIPASS` and ran.
    """
    started = time.monotonic()
    write_probe_wav(setup.local / PROBE_FILE)
    created = respond(smoke, _create_batch(setup.seen), {"PATH": setup.path})
    batch_id = _field(created[1], "batch", "summary", "batch_id")
    if not answered_ok(created) or batch_id is None:
        return judged(PROBE_CHECK, created[0], "create_batch did not answer ok")
    run, batch = _batch_actions(smoke, setup, batch_id)
    if not answered_ok((run, batch)):
        return judged(PROBE_CHECK, run, "a batch action did not answer ok")
    state = probed_item(batch)
    seconds = time.monotonic() - started
    if state != PROBED:
        detail = f"get_batch shows the WAV as {state}, expected {PROBED}\n{run.text()}"
        return failed(PROBE_CHECK, detail, seconds)
    return Outcome(PROBE_CHECK, passed=True, detail="", seconds=seconds)
