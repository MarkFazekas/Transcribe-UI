"""`MediaTools` on the real ffprobe and ffmpeg."""

from dataclasses import dataclass, field
from pathlib import Path

from transcribe_ui.api.ports import CancelToken
from transcribe_ui.media.classes import ConvertedAudio, ProbeResult
from transcribe_ui.media.constants import (
    CONVERT_TIMEOUT_S,
    EXTRACT_TIMEOUT_S,
    PROBE_TIMEOUT_S,
    SLOWEST_READ_BYTES_PER_S,
)
from transcribe_ui.media.convert import convert
from transcribe_ui.media.extract import extract
from transcribe_ui.media.invoke import RunLimits, ToolInvoker, require_index
from transcribe_ui.media.probe import probe
from transcribe_ui.media.runner import ProcessRunner, SubprocessRunner
from transcribe_ui.media.tools import ToolLocator


@dataclass(frozen=True)
class MediaTimeouts:
    """Longest each kind of tool call may run before it is killed.

    Conversion and extraction read the whole file, so their limit grows with its size: at least
    the base, and as long as reading it at `min_bytes_per_s` takes.
    """

    probe_s: float = PROBE_TIMEOUT_S
    convert_s: float = CONVERT_TIMEOUT_S
    extract_s: float = EXTRACT_TIMEOUT_S
    min_bytes_per_s: float = SLOWEST_READ_BYTES_PER_S

    def scaled(self, base_s: float, source: Path) -> float:
        """Return the limit of a call that reads the whole source."""
        try:
            size = source.stat().st_size
        except OSError:
            return base_s
        return max(base_s, size / self.min_bytes_per_s)


@dataclass(frozen=True)
class FfmpegMediaTools:
    """The production `MediaTools`; tests inject the locator, the runner and short timeouts."""

    locator: ToolLocator = field(default_factory=ToolLocator)
    runner: ProcessRunner = field(default_factory=SubprocessRunner)
    timeouts: MediaTimeouts = field(default_factory=MediaTimeouts)

    def probe(self, source: Path, cancel: CancelToken) -> ProbeResult:
        """Return what ffprobe finds in the file."""
        return probe(self._invoker(), source, RunLimits(self.timeouts.probe_s, cancel))

    def convert(self, source: Path, track_index: int, output_base: Path, cancel: CancelToken) -> ConvertedAudio:
        """Write the audio track to `<output_base>.mka`, or `.flac` if Matroska refuses the codec."""
        require_index(track_index, "convert")
        limits = RunLimits(self.timeouts.scaled(self.timeouts.convert_s, source), cancel)
        return convert(self._invoker(), source, track_index, output_base, limits)

    def extract(self, source: Path, stream_index: int, cancel: CancelToken) -> str:
        """Return the text subtitle stream as SRT text (probing first for the start time)."""
        require_index(stream_index, "extract")
        start_s = self.probe(source, cancel).start_s
        limits = RunLimits(self.timeouts.scaled(self.timeouts.extract_s, source), cancel)
        return extract(self._invoker(), source, stream_index, limits, start_s)

    def _invoker(self) -> ToolInvoker:
        return ToolInvoker(self.locator, self.runner)
