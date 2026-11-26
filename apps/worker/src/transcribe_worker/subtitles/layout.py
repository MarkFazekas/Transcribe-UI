"""Text helpers of the cue builder: whitespace, punctuation at word ends and line splitting."""

from transcribe_worker.subtitles.constants import (
    CLAUSE_END_MARKS,
    CLOSING_CHARS,
    MAX_LINE_CHARS,
    SENTENCE_END_MARKS,
)


def normalise_whitespace(text: str) -> str:
    """Return the text stripped, with every run of whitespace collapsed to one space."""
    return " ".join(text.split())


def ends_sentence(word: str) -> bool:
    """Return True when the word ends with sentence punctuation (closing quotes are skipped)."""
    return word.rstrip(CLOSING_CHARS).endswith(SENTENCE_END_MARKS)


def ends_clause(word: str) -> bool:
    """Return True when the word ends with clause punctuation (closing quotes are skipped)."""
    return word.rstrip(CLOSING_CHARS).endswith(CLAUSE_END_MARKS)


def split_lines(text: str) -> tuple[str, ...] | None:
    """Lay normalised text out on at most two lines, or return None when it does not fit.

    One line when it fits (or is a single word, however long); otherwise the most balanced
    split at a space where both lines fit, a single overlong word counting as fitting. Ties
    prefer the shorter top line. Text is never dropped or cut.
    """
    if _line_fits(text):
        return (text,)
    words = text.split(" ")
    counts = range(1, len(words))
    splits = [_split(words, count) for count in counts]
    fitting = [split for split in splits if all(map(_line_fits, split))]
    return min(fitting, key=_balance, default=None)


def _split(words: list[str], count: int) -> tuple[str, str]:
    top = " ".join(words[:count])
    bottom = " ".join(words[count:])
    return top, bottom


def _line_fits(line: str) -> bool:
    return len(line) <= MAX_LINE_CHARS or " " not in line


def _balance(split: tuple[str, str]) -> tuple[int, int]:
    top, bottom = split
    difference = abs(len(top) - len(bottom))
    return difference, len(top)
