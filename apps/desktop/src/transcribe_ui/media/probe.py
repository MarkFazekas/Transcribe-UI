"""Probing a media file with ffprobe: duration, audio tracks and subtitle streams (§3.3 review)."""

import math
from pathlib import Path

from pydantic import ValidationError

from transcribe_ui.media.classes import (
    AudioTrack,
    FfprobeOutput,
    FfprobeStream,
    ProbeResult,
    SubtitleKind,
    SubtitleStream,
)
from transcribe_ui.media.constants import (
    AUDIO_STREAM_TYPE,
    BITMAP_SUBTITLE_CODECS,
    FFPROBE,
    PROBE_ARGUMENTS,
    PROBE_FAILED_MESSAGE,
    PROBE_UNREADABLE_MESSAGE,
    SUBTITLE_STREAM_TYPE,
    TEXT_SUBTITLE_CODECS,
    UNDETERMINED_LANGUAGE,
    UNKNOWN_CODEC,
    VIDEO_STREAM_TYPE,
)
from transcribe_ui.media.invoke import RunLimits, ToolInvoker, file_url, tool_failed


def probe(invoker: ToolInvoker, source: Path, limits: RunLimits) -> ProbeResult:
    """Return what ffprobe finds in the file.

    Raises:
        DomainError: `media_tool_failed` if ffprobe cannot read the file, or the errors of
            `ToolInvoker.run`.
    """
    arguments = [*PROBE_ARGUMENTS, file_url(source)]
    completed = invoker.run(FFPROBE, arguments, "probe", limits)
    if completed.returncode != 0:
        raise tool_failed(FFPROBE, "probe", completed, PROBE_FAILED_MESSAGE)
    try:
        output = FfprobeOutput.model_validate_json(completed.stdout)
    except ValidationError as error:
        raise tool_failed(FFPROBE, "probe", completed, PROBE_UNREADABLE_MESSAGE) from error
    return summarise(output)


def summarise(output: FfprobeOutput) -> ProbeResult:
    """Turn ffprobe's JSON into a `ProbeResult`."""
    streams = output.streams
    audio = [stream for stream in streams if stream.codec_type == AUDIO_STREAM_TYPE]
    subtitles = [stream for stream in streams if stream.codec_type == SUBTITLE_STREAM_TYPE]
    return ProbeResult(
        duration_s=_duration(output),
        start_s=_number(output.container.start_time),
        has_video=any(_is_real_video(stream) for stream in streams),
        audio_tracks=tuple(map(_audio_track, range(len(audio)), audio)),
        subtitle_streams=tuple(map(_subtitle_stream, range(len(subtitles)), subtitles)),
    )


def subtitle_kind(codec: str) -> SubtitleKind:
    """Return whether a subtitle codec is text (extractable), bitmap or something else."""
    if codec in TEXT_SUBTITLE_CODECS:
        return "text"
    if codec in BITMAP_SUBTITLE_CODECS:
        return "bitmap"
    return "other"


def _audio_track(index: int, stream: FfprobeStream) -> AudioTrack:
    channels = stream.channels
    if channels is not None and channels < 0:
        channels = None
    return AudioTrack(
        index=index,
        codec=_codec(stream),
        language=_language(stream),
        title=_tag(stream, "title"),
        default=_flag(stream, "default"),
        channels=channels,
    )


def _subtitle_stream(index: int, stream: FfprobeStream) -> SubtitleStream:
    codec = _codec(stream)
    return SubtitleStream(
        index=index,
        codec=codec,
        kind=subtitle_kind(codec),
        language=_language(stream),
        title=_tag(stream, "title"),
        default=_flag(stream, "default"),
        forced=_flag(stream, "forced"),
    )


def _is_real_video(stream: FfprobeStream) -> bool:
    return stream.codec_type == VIDEO_STREAM_TYPE and not _flag(stream, "attached_pic")


def _codec(stream: FfprobeStream) -> str:
    return stream.codec_name or UNKNOWN_CODEC


def _flag(stream: FfprobeStream, name: str) -> bool:
    return stream.disposition.get(name, 0) != 0


def _tag(stream: FfprobeStream, name: str) -> str | None:
    """Return a tag's value, whatever case the container stores its name in; blank is None."""
    for key, tag_value in stream.tags.items():
        if key.lower() == name:
            stripped = tag_value.strip()
            return stripped or None
    return None


def _language(stream: FfprobeStream) -> str | None:
    language = _tag(stream, "language")
    if language is None or language.lower() == UNDETERMINED_LANGUAGE:
        return None
    return language.lower()


def _duration(output: FfprobeOutput) -> float | None:
    container = _seconds(output.container.duration)
    if container is not None:
        return container
    durations = [_seconds(stream.duration) for stream in output.streams]
    return max((seconds for seconds in durations if seconds is not None), default=None)


def _seconds(text: str | None) -> float | None:
    """Return ffprobe's duration text as seconds; None for missing, "N/A", negative or nonsense."""
    seconds = _number(text)
    if seconds is None or seconds < 0:
        return None
    return seconds


def _number(text: str | None) -> float | None:
    """Return ffprobe's number text as a float; None for missing, "N/A" or not finite."""
    if text is None:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None
