"""Conversion of WhisperX result segments into a Transcript (pure; no GPU stack imports).

WhisperX returns one mapping per segment, `{"start", "end", "text"}` from transcription and,
after alignment, also `"words"`: `{"word", "start"?, "end"?, "score"?}` per word, where a word
the aligner could not place has no times. Times may be numpy scalars or NaN (pandas).
"""

import math
from collections.abc import Iterable, Mapping
from typing import SupportsFloat

from transcribe_worker.engine.classes import Segment, Transcript, Word


def build_transcript(segments: Iterable[Mapping[str, object]], language: str, *, aligned: bool) -> Transcript:
    """Return the transcript of WhisperX result segments.

    Segments without text or usable times are dropped; words without usable times are kept
    without times; an end before the start is moved to the start.
    """
    converted = (_segment(raw) for raw in segments)
    kept = tuple(segment for segment in converted if segment is not None)
    return Transcript(language=language, segments=kept, aligned=aligned)


def _segment(raw: Mapping[str, object]) -> Segment | None:
    text = _text(raw.get("text"))
    start = _time(raw.get("start"))
    end = _time(raw.get("end"))
    if not text or start is None or end is None:
        return None
    converted = (_word(word) for word in _word_mappings(raw.get("words")))
    words = tuple(word for word in converted if word is not None)
    latest = max(start, end)
    return Segment(start=start, end=latest, text=text, words=words)


def _word(raw: Mapping[str, object]) -> Word | None:
    text = _text(raw.get("word"))
    if not text:
        return None
    start = _time(raw.get("start"))
    end = _time(raw.get("end"))
    if start is None or end is None:
        return Word(text=text)
    return Word(text=text, start=start, end=max(start, end))


def _word_mappings(raw: object) -> list[Mapping[str, object]]:
    if not isinstance(raw, list):
        return []
    return [word for word in raw if isinstance(word, Mapping)]


def _text(raw: object) -> str:
    return raw.strip() if isinstance(raw, str) else ""


def _time(raw: object) -> float | None:
    """Return a finite time clamped to zero, or None for a missing, NaN or non-numeric value."""
    if isinstance(raw, bool) or not isinstance(raw, SupportsFloat):
        return None
    seconds = float(raw)
    if not math.isfinite(seconds):
        return None
    return max(seconds, 0)
