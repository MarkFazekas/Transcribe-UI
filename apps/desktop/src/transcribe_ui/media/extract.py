"""Extracting an embedded text subtitle stream as SRT (architecture §3.4).

ffmpeg writes the SRT to its stdout, so there is no temp file to clean up. `-copyts` keeps the
cue times as the container stores them: without it ffmpeg shifts them by the file's start time
(often a few tens of milliseconds, from audio priming), and the subtitle would run late.
"""

from pathlib import Path

from transcribe_ui.media.constants import EXTRACT_FAILED_MESSAGE, FFMPEG, QUIET_ARGUMENTS, SRT_FORMAT, STDOUT_OUTPUT
from transcribe_ui.media.invoke import RunLimits, ToolInvoker, file_url, tool_failed


def extract(invoker: ToolInvoker, source: Path, stream_index: int, limits: RunLimits) -> str:
    """Return subtitle stream `stream_index` of `source` as SRT text.

    Raises:
        DomainError: `media_tool_failed` if ffmpeg cannot convert the stream (a bitmap codec, no
            such stream), or the errors of `ToolInvoker.run`.
    """
    arguments = [*QUIET_ARGUMENTS, "-copyts", "-i", file_url(source)]
    arguments += ["-map", f"0:s:{stream_index}", "-c:s", SRT_FORMAT, "-f", SRT_FORMAT, STDOUT_OUTPUT]
    completed = invoker.run(FFMPEG, arguments, "extract", limits)
    if completed.returncode != 0:
        raise tool_failed(FFMPEG, "extract", completed, EXTRACT_FAILED_MESSAGE)
    return completed.stdout.decode("utf-8", errors="replace")
