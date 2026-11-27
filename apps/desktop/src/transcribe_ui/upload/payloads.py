"""Pydantic payload of the `audio_objects` rows (the JSON column of `state/models.py`)."""

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
