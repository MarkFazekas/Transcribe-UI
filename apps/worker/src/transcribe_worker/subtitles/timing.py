"""Timed words of a transcript: engine times where known, estimated times elsewhere."""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from itertools import accumulate

from transcribe_worker.engine.classes import Segment, Transcript, Word
from transcribe_worker.subtitles.layout import normalise_whitespace


@dataclass(frozen=True, slots=True)
class TimedWord:
    """One word with times in seconds; `end >= start` and starts never decrease along a transcript."""

    text: str
    start: float
    end: float


def timed_words(transcript: Transcript) -> tuple[TimedWord, ...]:
    """Return every non-empty word of the transcript in order, each with times.

    Words the engine timed keep their times. A run of untimed words (alignment failed for
    them, or the segment has no words at all) shares the time between its timed neighbours
    (or the segment edges) in proportion to the word lengths. Afterwards starts are made
    non-decreasing and every end at least its start, which absorbs overlapping and
    zero-length engine times.
    """
    words = (word for segment in transcript.segments for word in _segment_words(segment))
    return tuple(_monotonic(words))


def _segment_words(segment: Segment) -> Iterator[TimedWord]:
    words = [word for word in _source_words(segment) if word.text]
    cursor = segment.start
    pending: list[str] = []
    for word in words:
        if word.start is None or word.end is None:
            pending.append(word.text)
            continue
        yield from _spread(pending, cursor, max(word.start, cursor))
        pending = []
        yield TimedWord(word.text, word.start, word.end)
        cursor = max(cursor, word.end)
    yield from _spread(pending, cursor, max(segment.end, cursor))


def _source_words(segment: Segment) -> Iterator[Word]:
    """Yield the segment's words with normalised text, or untimed words of its text if it has none."""
    if any(normalise_whitespace(word.text) for word in segment.words):
        for word in segment.words:
            yield word.model_copy(update={"text": normalise_whitespace(word.text)})
    else:
        yield from (Word(text=part) for part in segment.text.split())


def _spread(texts: Sequence[str], start: float, end: float) -> Iterator[TimedWord]:
    """Share start..end between the texts in proportion to their lengths."""
    total = sum(len(text) for text in texts)
    if not total:
        return
    span = end - start
    offsets = accumulate((len(text) for text in texts), initial=0)
    bounds = [start + span * chars / total for chars in offsets]
    for index, text in enumerate(texts):
        yield TimedWord(text, bounds[index], bounds[index + 1])


def _monotonic(words: Iterator[TimedWord]) -> Iterator[TimedWord]:
    previous_start: float = 0
    for word in words:
        start = max(word.start, previous_start)
        yield TimedWord(word.text, start, max(word.end, start))
        previous_start = start
