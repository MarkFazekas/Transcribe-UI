"""Cue builder: timed words grouped into readable subtitle cues (architecture §5.3)."""

import math
from collections.abc import Sequence

from transcribe_worker.engine.classes import Transcript
from transcribe_worker.subtitles.classes import Cue
from transcribe_worker.subtitles.constants import (
    BACKTRACK_MIN_CHARS,
    CLAUSE_BREAK_MIN_CHARS,
    CLAUSE_BREAK_MIN_DURATION_S,
    MAX_CUE_DELAY_S,
    MAX_CUE_DURATION_S,
    MIN_CUE_DURATION_S,
    MIN_VISIBLE_DURATION_S,
    PAUSE_BREAK_S,
)
from transcribe_worker.subtitles.layout import ends_clause, ends_sentence, split_lines
from transcribe_worker.subtitles.timing import TimedWord, timed_words

type Group = tuple[TimedWord, ...]


def build_cues(transcript: Transcript) -> tuple[Cue, ...]:
    """Build subtitle cues from a transcript; an empty transcript gives no cues.

    A cue has at most two lines of at most 42 characters (a longer word gets a line of its
    own) and lasts 1-7 s where the timing allows. Cues end at pauses, at sentence ends, at
    clause marks once they are long enough, and otherwise when the next word would not fit,
    preferring the last punctuation mark of the full cue. Cues are sorted and never overlap.
    Unspaced text (Chinese, Japanese, Thai, ...) is written without inserted spaces and may be
    broken between its units.
    """
    return _finalise(_group(timed_words(transcript)))


def _group(words: Sequence[TimedWord]) -> list[Group]:
    groups: list[Group] = []
    current: Group = ()
    for word in words:
        if current and _breaks_before(current, word):
            groups.append(current)
            current = ()
        while current and not _fits(current, word):
            head, current = _cut(current)
            groups.append(head)
        current = (*current, word)
    if current:
        groups.append(current)
    return groups


def _breaks_before(group: Group, word: TimedWord) -> bool:
    """Return True when a cue should end before the word although the word would still fit."""
    last = group[-1]
    if word.start - last.end > PAUSE_BREAK_S:
        return True
    duration = _end(group) - group[0].start
    starts_lower = word.text[:1].islower()
    if ends_sentence(last.text) and not starts_lower:
        return duration >= MIN_CUE_DURATION_S
    long_enough = len(_text(group)) >= CLAUSE_BREAK_MIN_CHARS or duration >= CLAUSE_BREAK_MIN_DURATION_S
    return ends_clause(last.text) and long_enough


def _fits(group: Group, word: TimedWord) -> bool:
    extended = (*group, word)
    if _end(extended) - group[0].start > MAX_CUE_DURATION_S:
        return False
    return split_lines(_text(extended)) is not None


def _cut(group: Group) -> tuple[Group, Group]:
    """Split a full cue after its last sentence end, else its last clause mark, else keep it whole.

    The first part needs BACKTRACK_MIN_CHARS characters, so no short fragment is left behind.
    """
    for ends_here in (ends_sentence, ends_clause):
        for index in range(len(group) - 1, 0, -1):
            head = group[:index]
            long_enough = len(_text(head)) >= BACKTRACK_MIN_CHARS
            if long_enough and ends_here(head[-1].text):
                return head, group[index:]
    return group, ()


def _finalise(groups: Sequence[Group]) -> tuple[Cue, ...]:
    """Turn groups into cues: minimum and maximum durations, sorted, no overlap."""
    if not groups:
        return ()
    starts = _starts([group[0].start for group in groups])
    limits = [*starts[1:], math.inf]
    cues: list[Cue] = []
    for group, start, limit in zip(groups, starts, limits, strict=True):
        lines = split_lines(_text(group))
        if lines is None:
            msg = "a cue does not fit on two lines"
            raise AssertionError(msg)
        end = _cue_end(group, start, limit)
        cues.append(Cue(start=start, end=end, lines=lines))
    return tuple(cues)


def _starts(own_starts: Sequence[float]) -> list[float]:
    """Return strictly increasing cue starts.

    A cue starts with its words, or later when the previous cue needs MIN_VISIBLE_DURATION_S.
    That delay never exceeds MAX_CUE_DELAY_S: beyond it, the pile of cues since the last
    undelayed one shares the time evenly (each shorter than MIN_VISIBLE_DURATION_S), so
    overlapping engine times cannot shift all later cues.
    """
    starts: list[float] = []
    pile = 0
    for index, own in enumerate(own_starts):
        earliest = starts[-1] + MIN_VISIBLE_DURATION_S if starts else own
        if earliest <= own:
            pile = index
        latest = own + MAX_CUE_DELAY_S
        if earliest > latest:
            starts = _shared(starts, pile, latest)
        else:
            starts.append(max(earliest, own))
    return starts


def _shared(starts: Sequence[float], pile: int, latest: float) -> list[float]:
    """Return the starts with those from `pile` on spread evenly up to `latest`, the start of one more cue."""
    first = starts[pile]
    count = len(starts) - pile
    step = (latest - first) / count
    spread = (first + step * offset for offset in range(count))
    return [*starts[:pile], *spread, latest]


def _cue_end(group: Group, start: float, limit: float) -> float:
    """Return the cue end: 1-7 s after the start where possible, never after the next cue's start."""
    wanted = max(_end(group), start + MIN_CUE_DURATION_S)
    return min(wanted, start + MAX_CUE_DURATION_S, limit)


def _text(group: Group) -> str:
    """Return the group's words joined by spaces, except before joined (unspaced) words."""
    return "".join(word.written() for word in group).removeprefix(" ")


def _end(group: Group) -> float:
    return max(word.end for word in group)
