"""Scripts written without spaces between words (Chinese, Japanese, Thai, ...): joining and break points.

Whether text is unspaced is decided by its characters, not by the transcript language: Whisper
output for Japanese or Chinese may contain Latin words, and spaced text may quote an unspaced
word. A boundary next to an unspaced character needs no space and may be broken (a line or a
cue may end there), except where the line-breaking rules of these scripts forbid it: before
closing punctuation, small kana and combining marks, and after opening punctuation.
"""

import unicodedata
from collections.abc import Iterator
from itertools import pairwise

from transcribe_worker.subtitles.constants import (
    MARK_CATEGORY_PREFIX,
    NO_BREAK_AFTER,
    NO_BREAK_BEFORE,
    PUNCTUATION_CATEGORY_PREFIX,
    UNSPACED_RANGES,
)


def is_unspaced(char: str) -> bool:
    """Return True when the character belongs to a script written without spaces between words."""
    code = ord(char)
    return any(first <= code <= last for first, last in UNSPACED_RANGES)


def ends_unspaced_punctuation(text: str) -> bool:
    """Return True when the text ends with punctuation of an unspaced script (such as "。")."""
    last = text[-1]
    return is_unspaced(last) and unicodedata.category(last).startswith(PUNCTUATION_CATEGORY_PREFIX)


def joins(left: str, right: str) -> bool:
    """Return True when the right text follows the left one without a space (either side is unspaced)."""
    return is_unspaced(left[-1]) or is_unspaced(right[0])


def can_break(left: str, right: str) -> bool:
    """Return True when text written without a space between the two characters may be broken there.

    Only at an unspaced boundary, and not before closing marks or combining marks nor after
    opening marks.
    """
    if not joins(left, right) or right in NO_BREAK_BEFORE or left in NO_BREAK_AFTER:
        return False
    return not unicodedata.category(right).startswith(MARK_CATEGORY_PREFIX)


def break_points(text: str) -> Iterator[tuple[int, int]]:
    """Yield where normalised text may be split into two lines, as (end of the top, start of the bottom).

    At a space the space is dropped; between two characters of unspaced text nothing is.
    """
    for index, (left, right) in enumerate(pairwise(text), start=1):
        if right == " ":
            yield index, index + 1
        elif left != " " and can_break(left, right):
            yield index, index


def is_breakable(text: str) -> bool:
    """Return True when the text has a break point."""
    return next(break_points(text), None) is not None


def units(text: str) -> list[str]:
    """Split text at its unspaced break points into the smallest pieces a cue may start or end with.

    Spaced text and text with spaces in it stay one piece at spaces; only unspaced boundaries split.
    """
    cuts = [start for end, start in break_points(text) if end == start]
    bounds = [0, *cuts, len(text)]
    return [text[start:end] for start, end in pairwise(bounds)]
