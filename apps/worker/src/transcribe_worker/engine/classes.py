"""Transcript returned by a transcription engine (times in seconds from the audio start)."""

from typing import Self

from pydantic import BaseModel, ConfigDict, NonNegativeFloat, model_validator


class EngineModel(BaseModel):
    """Base of the engine models: immutable, unknown fields refused."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Word(EngineModel):
    """One recognised word; times are None when alignment could not place it."""

    text: str
    start: NonNegativeFloat | None = None
    end: NonNegativeFloat | None = None

    @property
    def timed(self) -> bool:
        """Return True when the word has start and end times."""
        return self.start is not None

    @model_validator(mode="after")
    def _check_times(self) -> Self:
        if (self.start is None) != (self.end is None):
            msg = "a word has both times or neither"
            raise ValueError(msg)
        return self


class Segment(EngineModel):
    """One stretch of speech as the engine returned it."""

    start: NonNegativeFloat
    end: NonNegativeFloat
    text: str
    words: tuple[Word, ...] = ()


class Transcript(EngineModel):
    """Whole transcript of one audio file in one language."""

    language: str
    segments: tuple[Segment, ...]
    aligned: bool

    @property
    def text(self) -> str:
        """Return the full text, segments joined by spaces."""
        texts = (segment.text.strip() for segment in self.segments)
        return " ".join(text for text in texts if text)
