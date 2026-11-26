"""Subtitle models."""

from typing import Self

from pydantic import BaseModel, ConfigDict, NonNegativeFloat, model_validator

from transcribe_worker.subtitles.constants import MAX_CUE_LINES


class Cue(BaseModel):
    """One subtitle cue: times in seconds from the audio start and one or two lines of text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: NonNegativeFloat
    end: NonNegativeFloat
    lines: tuple[str, ...]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.end <= self.start:
            msg = "a cue ends after it starts"
            raise ValueError(msg)
        if not 1 <= len(self.lines) <= MAX_CUE_LINES:
            msg = "a cue has one or two lines"
            raise ValueError(msg)
        if not all(map(_is_clean_line, self.lines)):
            msg = "cue lines are non-empty, on one line and with normalised whitespace"
            raise ValueError(msg)
        return self


def _is_clean_line(line: str) -> bool:
    return bool(line) and line == " ".join(line.split())
