"""Models of the media tools: probe results and conversion outputs, plus the raw ffprobe JSON."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat, NonNegativeInt

# What an embedded subtitle stream is: `text` can be extracted to SRT; `bitmap` (PGS, VobSub, ...)
# and `other` (codecs ffmpeg cannot turn into SRT) can only be transcribed.
type SubtitleKind = Literal["text", "bitmap", "other"]
# Container of a converted audio track: Matroska (stream copy) or FLAC (the fallback).
type AudioExtension = Literal["mka", "flac"]
# The external tools of the adapter.
type Tool = Literal["ffmpeg", "ffprobe"]
# What a tool was asked to do; part of error details.
type Operation = Literal["probe", "convert", "extract", "version"]


class MediaModel(BaseModel):
    """Base of the media models: immutable, unknown fields refused."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class AudioTrack(MediaModel):
    """One audio stream of a media file."""

    # Position among the file's audio streams (ffmpeg `-map 0:a:<index>`).
    index: NonNegativeInt
    codec: str
    # Language tag as stored in the file, lower case; None when missing or "und".
    language: str | None
    title: str | None
    default: bool
    channels: NonNegativeInt | None


class SubtitleStream(MediaModel):
    """One embedded subtitle stream of a media file."""

    # Position among the file's subtitle streams (ffmpeg `-map 0:s:<index>`).
    index: NonNegativeInt
    codec: str
    kind: SubtitleKind
    # Language tag as stored in the file, lower case; None when missing or "und".
    language: str | None
    title: str | None
    default: bool
    forced: bool


class ProbeResult(MediaModel):
    """What ffprobe found in a media file."""

    # Container duration (else the longest stream's); None when the file does not say.
    duration_s: NonNegativeFloat | None
    # Container start time (MPEG-TS often starts later than zero); None when the file does not say.
    start_s: float | None
    # Whether the file has a real video stream (cover art does not count).
    has_video: bool
    audio_tracks: tuple[AudioTrack, ...]
    subtitle_streams: tuple[SubtitleStream, ...]


class ConvertedAudio(MediaModel):
    """An audio track written to its own file."""

    path: Path
    extension: AudioExtension
    # True when the Matroska muxer refused the codec and the track was encoded to FLAC.
    fell_back: bool


class FfprobeModel(BaseModel):
    """Base of the raw ffprobe JSON models: fields we do not use are ignored."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class FfprobeStream(FfprobeModel):
    """One entry of ffprobe's `streams`."""

    codec_type: str | None = None
    codec_name: str | None = None
    channels: int | None = None
    duration: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    disposition: dict[str, int] = Field(default_factory=dict)


class FfprobeFormat(FfprobeModel):
    """ffprobe's `format` (the container)."""

    duration: str | None = None
    start_time: str | None = None


class FfprobeOutput(FfprobeModel):
    """The JSON document of `ffprobe -show_format -show_streams`."""

    streams: tuple[FfprobeStream, ...] = ()
    container: FfprobeFormat = Field(default_factory=FfprobeFormat, alias="format")
