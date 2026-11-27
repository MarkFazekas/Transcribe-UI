"""`MediaTools` on the real ffprobe and ffmpeg."""

from dataclasses import dataclass, field
from pathlib import Path

from transcribe_ui.api.ports import CancelToken
from transcribe_ui.media.classes import ConvertedAudio, ProbeResult
from transcribe_ui.media.constants import CONVERT_TIMEOUT_S, EXTRACT_TIMEOUT_S, PROBE_TIMEOUT_S
from transcribe_ui.media.convert import convert
from transcribe_ui.media.extract import extract
from transcribe_ui.media.invoke import RunLimits, ToolInvoker
from transcribe_ui.media.probe import probe
from transcribe_ui.media.runner import ProcessRunner, SubprocessRunner
from transcribe_ui.media.tools import ToolLocator


@dataclass(frozen=True)
class MediaTimeouts:
    """Longest each kind of tool call may run before it is killed."""

    probe_s: float = PROBE_TIMEOUT_S
    convert_s: float = CONVERT_TIMEOUT_S
    extract_s: float = EXTRACT_TIMEOUT_S


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
        limits = RunLimits(self.timeouts.convert_s, cancel)
        return convert(self._invoker(), source, track_index, output_base, limits)

    def extract(self, source: Path, stream_index: int, cancel: CancelToken) -> str:
        """Return the text subtitle stream as SRT text."""
        limits = RunLimits(self.timeouts.extract_s, cancel)
        return extract(self._invoker(), source, stream_index, limits)

    def _invoker(self) -> ToolInvoker:
        return ToolInvoker(self.locator, self.runner)
