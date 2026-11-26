"""Timed words of a transcript: engine times where known, estimated times elsewhere."""

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from itertools import accumulate, pairwise, repeat
from typing import Self

from transcribe_worker.engine.classes import Segment, Transcript, Word
from transcribe_worker.subtitles.layout import normalise_whitespace
from transcribe_worker.subtitles.script import can_break, ends_unspaced_punctuation, is_unspaced, joins, units


@dataclass(frozen=True, slots=True)
class TimedWord:
    """One word with times in seconds; `end >= start` and starts never decrease along a transcript.

    `joined` marks a word written right after the previous one without a space (unspaced scripts).
    """

    text: str
    start: float
    end: float
    joined: bool = False

    def written(self) -> str:
        """Return the text as written after the previous word: with a leading space unless joined."""
        return self.text if self.joined else f" {self.text}"

    def starting_at(self, start: float) -> Self:
        """Return the word starting no earlier than `start`, its end at least its start."""
        later = max(self.start, start)
        return replace(self, start=later, end=max(self.end, later))

    def merged(self, other: Self) -> Self:
        """Return this word with the other one appended without a space, spanning both."""
        end = max(self.end, other.end)
        return replace(self, text=self.text + other.text, end=end)


@dataclass(frozen=True, slots=True)
class _Piece:
    """A word or a unit of an unspaced word, before it gets times."""

    text: str
    joined: bool

    def timed(self, start: float, end: float) -> TimedWord:
        """Return the piece with times."""
        return TimedWord(self.text, start, end, self.joined)


def timed_words(transcript: Transcript) -> tuple[TimedWord, ...]:
    """Return every non-empty word of the transcript in order, each with times.

    Words the engine timed keep their times. A run of untimed words (alignment failed for
    them, or the segment has no words at all) shares the time between its timed neighbours
    (or the segment edges) in proportion to the word lengths. Afterwards starts are made
    non-decreasing and every end at least its start, which absorbs overlapping and
    zero-length engine times.

    Unspaced text (see `script`) is split into units, so a long unspaced word still fits on
    lines and in cues; a timed word shares its time between its units. Units and words that
    may not be broken apart (a character and its closing punctuation) are merged.
    """
    segments = (list(_merge_unbreakable(_segment_words(segment))) for segment in transcript.segments)
    words = (word for segment_words in _join_segments(segments) for word in segment_words)
    return tuple(_monotonic(words))


def _join_segments(segments: Iterable[list[TimedWord]]) -> Iterator[list[TimedWord]]:
    """Join a segment's first word to the previous segment after unspaced punctuation ("。", "、")."""
    previous: TimedWord | None = None
    for words in segments:
        if words and previous is not None and ends_unspaced_punctuation(previous.text):
            words[0] = replace(words[0], joined=True)
        previous = words[-1] if words else previous
        yield words


def _segment_words(segment: Segment) -> Iterator[TimedWord]:
    cursor = segment.start
    pending: list[_Piece] = []
    words = _source_words(segment)
    flags = _joined_flags(segment.text, words)
    for word, joined in zip(words, flags, strict=True):
        pieces = _pieces(word.text, joined=joined)
        if word.start is None or word.end is None:
            pending.extend(pieces)
            continue
        yield from _spread(pending, cursor, max(word.start, cursor))
        pending = []
        yield from _place(pieces, word.start, word.end)
        cursor = max(cursor, word.end)
    yield from _spread(pending, cursor, max(segment.end, cursor))


def _source_words(segment: Segment) -> list[Word]:
    """Return the segment's non-empty words with normalised text, or untimed words of its text if it has none."""
    normalised = [_normalised(word) for word in segment.words]
    words = [word for word in normalised if word.text]
    return words or [Word(text=part) for part in segment.text.split()]


def _normalised(word: Word) -> Word:
    text = normalise_whitespace(word.text)
    return word.model_copy(update={"text": text})


def _joined_flags(source: str, words: Sequence[Word]) -> Iterator[bool]:
    """Yield for each word whether it is written right after the previous one, without a space.

    That is the case when the segment text has the two without whitespace between them (as
    with per-character words of unspaced scripts) or, for a word not found in the segment
    text, at an unspaced boundary. Spaced text (a segment without unspaced characters) is never
    joined, nor is the first word of a segment here.
    """
    previous = ""
    for word, written_joined in zip(words, _written_flags(source, words), strict=False):
        yield _is_joined(previous, word.text, written_joined=written_joined)
        previous = word.text


def _written_flags(source: str, words: Sequence[Word]) -> Iterator[bool | None]:
    """Return `_written_joined` for text with unspaced characters; spaced text gives no information."""
    if any(map(is_unspaced, source)):
        return _written_joined(source, words)
    return repeat(None)


def _is_joined(previous: str, text: str, *, written_joined: bool | None) -> bool:
    if not previous:
        return False
    if written_joined is None:
        return joins(previous, text)
    return written_joined


def _written_joined(source: str, words: Sequence[Word]) -> Iterator[bool | None]:
    """Yield for each word whether the source text has it right after the previous word; None when not found."""
    position = 0
    for word in words:
        found = source.find(word.text, position)
        if found < 0:
            yield None
            continue
        gap = source[position:found]
        yield not any(map(str.isspace, gap))
        position = found + len(word.text)


def _pieces(text: str, *, joined: bool) -> list[_Piece]:
    """Return the word's units, the first joined as the word is and the others joined to it."""
    first, *rest = units(text)
    rest_pieces = [_Piece(unit, joined=True) for unit in rest]
    return [_Piece(first, joined=joined), *rest_pieces]


def _place(pieces: Sequence[_Piece], start: float, end: float) -> Iterator[TimedWord]:
    """Give a timed word's times to its only piece, or share them between its units."""
    if len(pieces) > 1:
        return _spread(pieces, start, end)
    return (piece.timed(start, end) for piece in pieces)


def _spread(pieces: Sequence[_Piece], start: float, end: float) -> Iterator[TimedWord]:
    """Share start..end between the pieces in proportion to their lengths."""
    total = sum(len(piece.text) for piece in pieces)
    if not total:
        return
    span = end - start
    offsets = accumulate((len(piece.text) for piece in pieces), initial=0)
    bounds = [start + span * chars / total for chars in offsets]
    spans = pairwise(bounds)
    for piece, (first, last) in zip(pieces, spans, strict=True):
        yield piece.timed(first, last)


def _merge_unbreakable(words: Iterable[TimedWord]) -> Iterator[TimedWord]:
    """Merge a joined word into the previous one when the text may not be broken between them."""
    previous: TimedWord | None = None
    for word in words:
        if previous is None:
            previous = word
        elif _unbreakable(previous, word):
            previous = previous.merged(word)
        else:
            yield previous
            previous = word
    if previous is not None:
        yield previous


def _unbreakable(previous: TimedWord, word: TimedWord) -> bool:
    left = previous.text[-1]
    right = word.text[0]
    return word.joined and not can_break(left, right)


def _monotonic(words: Iterator[TimedWord]) -> Iterator[TimedWord]:
    previous_start: float = 0
    for word in words:
        moved = word.starting_at(previous_start)
        yield moved
        previous_start = moved.start
