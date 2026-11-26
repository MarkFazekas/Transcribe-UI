"""Text helpers of the cue builder: whitespace, punctuation at word ends and line splitting."""

from transcribe_worker.subtitles.constants import (
    CLAUSE_END_MARKS,
    CLOSING_CHARS,
    MAX_LINE_CHARS,
    MAX_ORDINAL_DIGITS,
    SENTENCE_END_MARKS,
)
from transcribe_worker.subtitles.script import break_points, is_breakable


def normalise_whitespace(text: str) -> str:
    """Return the text stripped, with every run of whitespace collapsed to one space."""
    return " ".join(text.split())


def ends_sentence(word: str) -> bool:
    """Return True when the word ends with sentence punctuation (closing quotes are skipped).

    A dot after an abbreviation or an ordinal does not end a sentence: a single letter ("J."),
    letters separated by dots ("U.S.") or one or two digits ("3."); a year ("2023.") does.
    """
    stripped = word.rstrip(CLOSING_CHARS)
    return stripped.endswith(SENTENCE_END_MARKS) and not _is_abbreviation(stripped)


def ends_clause(word: str) -> bool:
    """Return True when the word ends with clause punctuation (closing quotes are skipped)."""
    return word.rstrip(CLOSING_CHARS).endswith(CLAUSE_END_MARKS)


def split_lines(text: str) -> tuple[str, ...] | None:
    """Lay normalised text out on at most two lines, or return None when it does not fit.

    One line when it fits (or cannot be broken, like a single word, however long); otherwise
    the most balanced split at a break point (a space, or a boundary in unspaced text) where
    both lines fit, an unbreakable overlong line counting as fitting. Ties prefer the shorter
    top line. Text is never dropped or cut.
    """
    if _line_fits(text):
        return (text,)
    splits = [_split(text, point) for point in break_points(text)]
    fitting = [split for split in splits if all(map(_line_fits, split))]
    return min(fitting, key=_balance, default=None)


def _is_abbreviation(word: str) -> bool:
    """Return True for "J.", "U.S." or "3." (the word ends with a dot), not for "2023."."""
    if not word.endswith("."):
        return False
    body = word.removesuffix(".")
    if body.isdecimal():
        return len(body) <= MAX_ORDINAL_DIGITS
    return all(map(_is_letter, body.split(".")))


def _is_letter(text: str) -> bool:
    return len(text) == 1 and text.isalpha()


def _split(text: str, point: tuple[int, int]) -> tuple[str, str]:
    top_end, bottom_start = point
    return text[:top_end], text[bottom_start:]


def _line_fits(line: str) -> bool:
    return len(line) <= MAX_LINE_CHARS or not is_breakable(line)


def _balance(split: tuple[str, str]) -> tuple[int, int]:
    top, bottom = split
    difference = abs(len(top) - len(bottom))
    return difference, len(top)
