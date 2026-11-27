"""Pydantic payloads of the upload: `audio_objects` and `extracted_subtitles` rows, and retry copies."""

from pydantic import NonNegativeFloat, NonNegativeInt, PositiveInt

from transcribe_contracts.classes import AudioExtension
from transcribe_ui.api.base import FrozenModel


class AudioObjectPayload(FrozenModel):
    """What an `audio_objects` row keeps besides its key and status: the manifest's audio entry."""

    # Audio track of the source video; None for an audio file uploaded as it is.
    track_index: NonNegativeInt | None
    extension: AudioExtension
    codec: str
    duration_s: NonNegativeFloat
    size_bytes: PositiveInt


class CopySource(FrozenModel):
    """An audio object of another batch that a retry item copies server side instead of uploading (M5.14)."""

    source_key: str
    # The copied object's manifest entry: the same track, extension, codec, duration and size.
    audio: AudioObjectPayload


class ExtractedSubtitlePayload(FrozenModel):
    """What an `extracted_subtitles` row keeps besides the SRT text."""

    # The embedded subtitle stream it was extracted from (among the file's subtitle streams).
    stream_index: NonNegativeInt
