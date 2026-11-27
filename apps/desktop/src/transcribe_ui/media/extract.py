"""Extracting an embedded text subtitle stream as SRT (architecture §3.4).

ffmpeg writes the SRT to its stdout, so there is no temp file to clean up. For a container that
starts at (about) zero, `-copyts` keeps the cue times as stored: without it ffmpeg shifts them by
the file's start time (often a few tens of milliseconds from audio priming), and the subtitle
would run late. A container that starts later (MPEG-TS and M2TS often start at a second or more)
has that start subtracted, which is ffmpeg's default without `-copyts`, since players count the
time from the start of the file.
"""

from pathlib import Path

from transcribe_ui.media.constants import (
    EXTRACT_FAILED_MESSAGE,
    FFMPEG,
    QUIET_ARGUMENTS,
    SRT_FORMAT,
    START_SHIFT_THRESHOLD_S,
    STDOUT_OUTPUT,
)
from transcribe_ui.media.invoke import RunLimits, ToolInvoker, file_url, tool_failed


def extract(invoker: ToolInvoker, source: Path, stream_index: int, limits: RunLimits, start_s: float | None) -> str:
    """Return subtitle stream `stream_index` of `source` as SRT text.

    `start_s` is the container's start time from the probe (None if unknown).

    Raises:
        DomainError: `media_tool_failed` if ffmpeg cannot convert the stream (a bitmap codec, no
            such stream), or the errors of `ToolInvoker.run`.
    """
    keep_times = start_s is None or abs(start_s) <= START_SHIFT_THRESHOLD_S
    timing = ["-copyts"] if keep_times else []
    arguments = [*QUIET_ARGUMENTS, *timing, "-i", file_url(source)]
    arguments += ["-map", f"0:s:{stream_index}", "-c:s", SRT_FORMAT, "-f", SRT_FORMAT, STDOUT_OUTPUT]
    completed = invoker.run(FFMPEG, arguments, "extract", limits)
    if completed.returncode != 0:
        raise tool_failed(FFMPEG, "extract", completed, EXTRACT_FAILED_MESSAGE)
    return completed.stdout.decode("utf-8", errors="replace")
