"""Pydantic payloads of the upload: `audio_objects` and `extracted_subtitles` rows, and retry copies."""

import re
from typing import Self

from pydantic import NonNegativeFloat, NonNegativeInt, PositiveInt, model_validator

from transcribe_contracts.classes import AudioExtension
from transcribe_contracts.constants import AUDIO_FOLDER, BATCH_ID_PATTERN, ULID_PATTERN
from transcribe_contracts.layout import audio_key
from transcribe_ui.api.base import FrozenModel

# The parts of an audio key: `<batch_id>/audio/<name>`.
KEY_PARTS = ("batch_id", "audio", "name")


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

    # The source object's key: an audio key of the R2 layout whose track and extension are `audio`'s.
    source_key: str
    # The copied object's manifest entry: the same track, extension, codec, duration and size.
    audio: AudioObjectPayload

    @model_validator(mode="after")
    def _check_key(self) -> Self:
        if self.source_key != _layout_key(self.source_key, self.audio):
            msg = "source_key must be the audio key of its track and extension in the R2 layout"
            raise ValueError(msg)
        return self


def _layout_key(key: str, audio: AudioObjectPayload) -> str | None:
    """Return the layout's audio key for the batch and item named in `key`, or None if it names none."""
    parts = key.split("/")
    shaped = len(parts) == len(KEY_PARTS)
    folder = parts[1] if shaped else None
    if folder != AUDIO_FOLDER:
        return None
    batch_id = parts[0]
    item_id = parts[-1].split(".", 1)[0]
    batch_ok = re.fullmatch(BATCH_ID_PATTERN, batch_id) is not None
    if not batch_ok or re.fullmatch(ULID_PATTERN, item_id) is None:
        return None
    return audio_key(batch_id, item_id, audio.extension, audio.track_index)


class ExtractedSubtitlePayload(FrozenModel):
    """What an `extracted_subtitles` row keeps besides the SRT text."""

    # The embedded subtitle stream it was extracted from (among the file's subtitle streams).
    stream_index: NonNegativeInt
