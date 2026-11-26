"""SRT writer."""

from collections.abc import Sequence

from transcribe_worker.subtitles.classes import Cue
from transcribe_worker.subtitles.constants import MS_PER_SECOND, SECONDS_PER_HOUR, SECONDS_PER_MINUTE


def to_srt(cues: Sequence[Cue]) -> str:
    """Return the cues as SRT text (LF newlines, final newline); no cues give an empty string.

    Times are rounded to milliseconds; a cue that rounding would empty or push into the
    previous one is moved so every cue lasts at least 1 ms and none overlaps the previous.
    """
    blocks: list[str] = []
    previous_end_ms = 0
    for number, cue in enumerate(cues, start=1):
        start_ms = max(_to_ms(cue.start), previous_end_ms)
        end_ms = max(_to_ms(cue.end), start_ms + 1)
        times = f"{_timestamp(start_ms)} --> {_timestamp(end_ms)}"
        blocks.append(_block(number, times, cue.lines))
        previous_end_ms = end_ms
    return "\n".join(blocks)


def _block(number: int, times: str, lines: Sequence[str]) -> str:
    """Return one cue block, ending with a newline."""
    return "\n".join((str(number), times, *lines, ""))


def _to_ms(seconds: float) -> int:
    return round(seconds * MS_PER_SECOND)


def _timestamp(total_ms: int) -> str:
    total_s, ms = divmod(total_ms, MS_PER_SECOND)
    hours, rest_s = divmod(total_s, SECONDS_PER_HOUR)
    minutes, seconds = divmod(rest_s, SECONDS_PER_MINUTE)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"
