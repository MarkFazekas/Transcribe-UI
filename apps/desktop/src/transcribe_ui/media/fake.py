"""A deterministic `MediaTools` for tests and golden flows (no ffmpeg needed).

Probe results come from a table keyed by file name (else `default_fake_probe`); the outputs are
derived from the inputs only, so they are byte-identical on every OS. Failures look like the real
adapter's: a missing source gives `media_tool_failed`, a set token `cancelled`.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from transcribe_ui.api.errors import CancelledError, DomainError
from transcribe_ui.api.ports import CancelToken
from transcribe_ui.media.classes import (
    AudioExtension,
    AudioTrack,
    ConvertedAudio,
    Operation,
    ProbeResult,
    SubtitleStream,
    Tool,
)
from transcribe_ui.media.constants import (
    CONVERT_FAILED_MESSAGE,
    ERROR_MEDIA_TOOL_FAILED,
    EXTRACT_FAILED_MESSAGE,
    FAKE_DEFAULT_DURATION_S,
    FAKE_FAILURE_EXIT_CODE,
    FAKE_REFUSED_CODECS,
    FFMPEG,
    FFPROBE,
    FLAC_EXTENSION,
    MATROSKA_EXTENSION,
    PROBE_FAILED_MESSAGE,
)
from transcribe_ui.media.convert import output_path
from transcribe_ui.media.invoke import require_index


def default_fake_probe() -> ProbeResult:
    """Return what the fake reports for a file it has no entry for: one AAC track, no video."""
    track = AudioTrack(
        index=0,
        codec="aac",
        language=None,
        title=None,
        default=True,
        channels=None,
    )
    return ProbeResult(
        duration_s=FAKE_DEFAULT_DURATION_S,
        start_s=0,
        has_video=False,
        audio_tracks=(track,),
        subtitle_streams=(),
    )


@dataclass
class FakeMediaTools:
    """`MediaTools` without processes; `calls` records "<operation> <file name>" per call."""

    probes: Mapping[str, ProbeResult] = field(default_factory=dict)
    default_probe: ProbeResult = field(default_factory=default_fake_probe)
    refused_codecs: frozenset[str] = FAKE_REFUSED_CODECS
    calls: list[str] = field(default_factory=list)

    def probe(self, source: Path, cancel: CancelToken) -> ProbeResult:
        """Return the registered result for the file name, else the default."""
        self._begin("probe", FFPROBE, source, cancel)
        return self._probed(source)

    def convert(self, source: Path, track_index: int, output_base: Path, cancel: CancelToken) -> ConvertedAudio:
        """Write a small file naming the source and track; FLAC for a refused codec."""
        require_index(track_index, "convert")
        self._begin("convert", FFMPEG, source, cancel)
        tracks = self._probed(source).audio_tracks
        if track_index >= len(tracks):
            raise _failed(FFMPEG, "convert")
        codec = tracks[track_index].codec
        fell_back = codec in self.refused_codecs
        extension: AudioExtension = FLAC_EXTENSION if fell_back else MATROSKA_EXTENSION
        target = output_path(output_base, extension)
        target.parent.mkdir(parents=True, exist_ok=True)
        description = f"fake {extension} audio of {source.name}, track {track_index} ({codec})\n"
        target.write_bytes(description.encode())
        return ConvertedAudio(path=target, extension=extension, fell_back=fell_back)

    def extract(self, source: Path, stream_index: int, cancel: CancelToken) -> str:
        """Return a one-cue SRT naming the source and stream; only text streams can be extracted."""
        require_index(stream_index, "extract")
        self._begin("extract", FFMPEG, source, cancel)
        streams = self._probed(source).subtitle_streams
        stream: SubtitleStream | None = streams[stream_index] if stream_index < len(streams) else None
        if stream is None or stream.kind != "text":
            raise _failed(FFMPEG, "extract")
        language = stream.language or "und"
        return f"1\n00:00:00,000 --> 00:00:01,000\n{source.name} subtitle {stream_index} ({language})\n\n"

    def _begin(self, operation: Operation, tool: Tool, source: Path, cancel: CancelToken) -> None:
        if cancel.is_cancelled():
            raise CancelledError
        self.calls.append(f"{operation} {source.name}")
        if not source.is_file():
            raise _failed(tool, operation, "No such file or directory")

    def _probed(self, source: Path) -> ProbeResult:
        return self.probes.get(source.name, self.default_probe)


def _failure_message(operation: Operation) -> str:
    if operation == "probe":
        return PROBE_FAILED_MESSAGE
    if operation == "convert":
        return CONVERT_FAILED_MESSAGE
    return EXTRACT_FAILED_MESSAGE


def _failed(tool: Tool, operation: Operation, stderr: str = "") -> DomainError:
    details = {"tool": tool, "operation": operation, "exit_code": FAKE_FAILURE_EXIT_CODE, "stderr": stderr}
    return DomainError(ERROR_MEDIA_TOOL_FAILED, _failure_message(operation), details)
