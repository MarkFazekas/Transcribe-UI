"""Regression tests of the WhisperX result conversion (shapes as WhisperX 3.8.6 returns them)."""

import math

import pytest

from transcribe_worker.engine.classes import Transcript
from transcribe_worker.engine.convert import build_transcript

LANGUAGE = "hu"
SCORE = 0.912
AVG_LOG_PROB = -0.21
NUMBER = "1984"

type Span = tuple[object, object]
type Raw = dict[str, object]
type WordSpan = tuple[str, float | None, float | None]


def raw_word(text: str, span: Span | None = None) -> Raw:
    """Return an aligned word; WhisperX leaves out the times of a word it cannot place."""
    word: Raw = {"word": text, "score": SCORE}
    if span is not None:
        word["start"] = span[0]
        word["end"] = span[1]
    return word


def raw_segment(span: Span, text: object, words: list[Raw] | None = None) -> Raw:
    """Return a segment as the batched pipeline (no words) or the aligner (words) returns it."""
    segment: Raw = {"start": span[0], "end": span[1], "text": text, "avg_logprob": AVG_LOG_PROB}
    if words is not None:
        segment["words"] = words
        segment["chars"] = None
    return segment


def spans(transcript: Transcript) -> list[tuple[float, float, str]]:
    """Return (start, end, text) of every segment."""
    return [(segment.start, segment.end, segment.text) for segment in transcript.segments]


def word_spans(transcript: Transcript) -> list[list[WordSpan]]:
    """Return (text, start, end) of every word, per segment."""
    return [[(word.text, word.start, word.end) for word in segment.words] for segment in transcript.segments]


def test_batched_segments_are_unaligned() -> None:
    """Segment-level output (no alignment model): text stripped, no words."""
    raw = [raw_segment((0.031, 4.982), " Jó napot kívánok!"), raw_segment((5.5, 9.25), " Hogy van?")]

    transcript = build_transcript(raw, LANGUAGE, aligned=False)

    assert transcript.language == LANGUAGE
    assert not transcript.aligned
    assert spans(transcript) == [(0.031, 4.982, "Jó napot kívánok!"), (5.5, 9.25, "Hogy van?")]
    assert word_spans(transcript) == [[], []]
    assert transcript.text == "Jó napot kívánok! Hogy van?"


def test_aligned_words_keep_their_times() -> None:
    """Aligned output: every word with its times; extra keys (score, chars) are ignored."""
    words = [raw_word("Jó", (0.1, 0.32)), raw_word("napot", (0.36, 0.8))]

    transcript = build_transcript([raw_segment((0.1, 0.8), " Jó napot", words)], LANGUAGE, aligned=True)

    assert transcript.aligned
    assert word_spans(transcript) == [[("Jó", 0.1, 0.32), ("napot", 0.36, 0.8)]]


@pytest.mark.parametrize(
    "word",
    [
        raw_word(NUMBER),
        {"word": NUMBER, "start": 2.0},
        {"word": NUMBER, "end": 2.4},
        raw_word(NUMBER, (math.nan, math.nan)),
        raw_word(NUMBER, (2.0, math.inf)),
        raw_word(NUMBER, ("2.0", True)),
    ],
    ids=["no-times", "start-only", "end-only", "nan", "infinite", "not-numbers"],
)
def test_word_without_usable_times_is_kept(word: Raw) -> None:
    """A word the aligner could not place stays in the text, without times."""
    segment = raw_segment((1, 3), "Az 1984", [raw_word("Az", (1, 1.4)), word])

    transcript = build_transcript([segment], LANGUAGE, aligned=True)

    assert word_spans(transcript) == [[("Az", 1, 1.4), (NUMBER, None, None)]]
    placed, unplaced = transcript.segments[0].words
    assert placed.timed
    assert not unplaced.timed


def test_inverted_and_negative_times_are_clamped() -> None:
    """End before start becomes a zero-length span; a negative time becomes zero."""
    raw = [raw_segment((-0.01, 2), "Igen", [raw_word("Igen", (1.5, 1.2))]), raw_segment((3, 2.9), "Nem")]

    transcript = build_transcript(raw, LANGUAGE, aligned=True)

    assert spans(transcript) == [(0, 2, "Igen"), (3, 3, "Nem")]
    assert word_spans(transcript) == [[("Igen", 1.5, 1.5)], []]


@pytest.mark.parametrize(
    "raw",
    [
        [],
        [raw_segment((0, 1), "")],
        [raw_segment((0, 1), "   ", [])],
        [raw_segment((math.nan, math.nan), "Szia")],
        [raw_segment((None, 1), "Szia")],
        [raw_segment((0, 1), None)],
    ],
    ids=["no-segments", "empty-text", "blank-text", "nan-times", "missing-start", "missing-text"],
)
def test_unusable_segments_are_dropped(raw: list[Raw]) -> None:
    """Silence, blank text or unusable times leave no segment (an empty transcript is valid)."""
    transcript = build_transcript(raw, LANGUAGE, aligned=True)

    assert transcript == Transcript(language=LANGUAGE, aligned=True, segments=())
    assert not transcript.text


def test_blank_and_malformed_words_are_skipped() -> None:
    """Blank words, non-mapping entries and a non-list `words` value add no words."""
    raw = [
        raw_segment((0, 1), "Na", [raw_word(" ", (0, 0.1)), raw_word("Na", (0.2, 0.5))]),
        {"start": 1, "end": 2, "text": "jó", "words": [None, "jó"]},
        {"start": 2, "end": 3, "text": "van", "words": "van"},
    ]

    transcript = build_transcript(raw, LANGUAGE, aligned=True)

    assert word_spans(transcript) == [[("Na", 0.2, 0.5)], [], []]
