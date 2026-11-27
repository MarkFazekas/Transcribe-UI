"""The `MediaTools` port: what the backend asks of ffprobe and ffmpeg."""

from pathlib import Path
from typing import Protocol

from transcribe_ui.api.ports import CancelToken
from transcribe_ui.media.classes import ConvertedAudio, ProbeResult


class MediaTools(Protocol):
    """Probe, convert and extract; every call stops (and cleans up) when the token is set.

    Failures are `DomainError`s: `media_tool_not_found`, `media_tool_failed`,
    `media_tool_timeout` and `cancelled`.
    """

    def probe(self, source: Path, cancel: CancelToken) -> ProbeResult:
        """Return the file's duration, audio tracks and subtitle streams."""
        ...

    def convert(self, source: Path, track_index: int, output_base: Path, cancel: CancelToken) -> ConvertedAudio:
        """Write audio track `track_index` (among the audio streams) to `<output_base>.mka` or `.flac`.

        Nothing is left at the output on failure; an existing output of the same name is replaced.
        """
        ...

    def extract(self, source: Path, stream_index: int, cancel: CancelToken) -> str:
        """Return text subtitle stream `stream_index` (among the subtitle streams) as SRT text."""
        ...
