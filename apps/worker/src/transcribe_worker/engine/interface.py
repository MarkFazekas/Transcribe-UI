"""What the pipeline needs from a transcription engine."""

from pathlib import Path
from typing import Protocol

from transcribe_contracts.classes import EngineInfo
from transcribe_worker.engine.classes import Transcript


class Transcriber(Protocol):
    """A loaded transcription engine; one instance transcribes many files one after the other.

    Implementations keep the speech model loaded between calls and the alignment model of the
    last language; a call with another language releases the previous alignment model first.
    """

    def settings(self, *, careful: bool) -> EngineInfo:
        """Return the settings recorded with every result of the given mode."""

    def transcribe(self, audio: Path, language: str, *, careful: bool) -> Transcript:
        """Transcribe a 16 kHz mono WAV file in the given (forced) language.

        Args:
            audio: Decoded audio (16 kHz, mono, 16-bit PCM WAV).
            language: Spoken language code; no detection is done.
            careful: Use the slower, stricter decoding (beam search, temperature fallback,
                no conditioning on previous text) instead of the fast batched one.

        Raises:
            Exception: Any failure; the caller marks the job failed and continues.
        """
