"""Transcript quality: cleaning, and the checks that decide on a careful re-run (architecture §5.3).

The checks look for the usual Whisper failures: empty output, repetition loops and
hallucinated phrases repeated over silence or music. A false alarm only costs a re-run, so
they lean towards flagging, but normal speech passes.
"""

import zlib
from collections import Counter
from collections.abc import Iterator, Sequence
from itertools import groupby

from transcribe_worker.engine.classes import Transcript
from transcribe_worker.subtitles.constants import (
    COMPRESSION_WINDOW_CHARS,
    DOMINANT_MIN_COUNT,
    DOMINANT_SEGMENT_SHARE,
    MAX_COMPRESSION_RATIO,
    MAX_NGRAM_REPEATS,
    MAX_NGRAM_WORDS,
    MAX_SEGMENT_REPEATS,
    MIN_COMPRESSION_CHARS,
)


def clean(transcript: Transcript) -> Transcript:
    """Return the transcript without empty segments and without consecutive duplicate segments.

    Segments are duplicates when their texts match after case, punctuation and whitespace are
    ignored; the first of a run is kept.
    """
    kept = []
    previous_key = None
    for segment in transcript.segments:
        key = _key(segment.text)
        if key and key != previous_key:
            kept.append(segment)
        previous_key = key or previous_key
    return transcript.model_copy(update={"segments": tuple(kept)})


def find_problems(transcript: Transcript) -> tuple[str, ...]:
    """Return why the (uncleaned) transcript looks like a Whisper failure; empty when it looks fine."""
    text = transcript.text
    if not text:
        return ("empty output: no speech was recognised",)
    all_keys = [_key(segment.text) for segment in transcript.segments]
    keys = [key for key in all_keys if key]
    problems = (
        _compression_problem(text),
        _run_problem(keys),
        _dominance_problem(keys),
        _ngram_problem(all_keys),
    )
    return tuple(problem for problem in problems if problem)


def _key(text: str) -> str:
    """Return the text for comparisons: case-folded, punctuation dropped, whitespace collapsed.

    Text made of punctuation only (such as "...") is compared as it is, stripped.
    """
    kept = (char if char.isalnum() else " " for char in text.casefold())
    words = "".join(kept).split()
    return " ".join(words) or text.strip()


def _compression_problem(text: str) -> str | None:
    if len(text) < MIN_COMPRESSION_CHARS:
        return None
    ratio = max(_compression_ratio(window) for window in _windows(text))
    if ratio <= MAX_COMPRESSION_RATIO:
        return None
    return f"the text compresses {ratio:.1f}x (limit {MAX_COMPRESSION_RATIO}): it repeats itself"


def _windows(text: str) -> Iterator[str]:
    """Yield consecutive windows of the text, all full-length (the last one overlaps)."""
    if len(text) <= COMPRESSION_WINDOW_CHARS:
        yield text
        return
    for end in range(COMPRESSION_WINDOW_CHARS, len(text), COMPRESSION_WINDOW_CHARS):
        start = end - COMPRESSION_WINDOW_CHARS
        yield text[start:end]
    yield text[-COMPRESSION_WINDOW_CHARS:]


def _compression_ratio(text: str) -> float:
    encoded = text.encode()
    return len(encoded) / len(zlib.compress(encoded))


def _run_problem(keys: Sequence[str]) -> str | None:
    longest = (0, "")
    for run_key, run in groupby(keys):
        longest = max(longest, (len(list(run)), run_key))
    count, key = longest
    if count < MAX_SEGMENT_REPEATS:
        return None
    return f"the same segment text repeats {count} times in a row: {key!r}"


def _dominance_problem(keys: Sequence[str]) -> str | None:
    if not keys:
        return None
    key, count = Counter(keys).most_common(1)[0]
    if count < DOMINANT_MIN_COUNT or count <= len(keys) * DOMINANT_SEGMENT_SHARE:
        return None
    return f"one segment text makes up {count} of {len(keys)} segments: {key!r}"


def _ngram_problem(keys: Sequence[str]) -> str | None:
    for number, key in enumerate(keys, start=1):
        ngram = _repeated_ngram(key.split())
        if ngram is not None:
            return f"segment {number} repeats {ngram!r} {MAX_NGRAM_REPEATS} or more times in a row"
    return None


def _repeated_ngram(words: Sequence[str]) -> str | None:
    """Return the first n-gram (up to MAX_NGRAM_WORDS words) repeated MAX_NGRAM_REPEATS times in a row."""
    for size in range(1, MAX_NGRAM_WORDS + 1):
        span = size * MAX_NGRAM_REPEATS
        for end in range(span, len(words) + 1):
            start = end - span
            window = list(words[start:end])
            if window == window[:size] * MAX_NGRAM_REPEATS:
                return " ".join(window[:size])
    return None
