"""Constants of the subtitle cue builder, the SRT writer and the transcript quality checks."""

from typing import Final

# Longest subtitle line in characters (a single longer word still gets a line of its own).
MAX_LINE_CHARS: Final = 42
# Most lines in one cue.
MAX_CUE_LINES: Final = 2
# Longest cue in seconds; a group of words never spans more, and a lone word is cut to it.
MAX_CUE_DURATION_S: Final = 7.0
# Shortest cue in seconds; shorter cues are extended up to this, never into the next cue.
MIN_CUE_DURATION_S: Final = 1.0
# Shortest cue kept when the next cue leaves no room (later cues move instead), so no cue collapses.
MIN_VISIBLE_DURATION_S: Final = 0.1
# Silence between two words (seconds) that always starts a new cue.
PAUSE_BREAK_S: Final = 0.6
# Characters a cue needs before a clause mark (, ; :) ends it.
CLAUSE_BREAK_MIN_CHARS: Final = 30
# Seconds a cue needs before a clause mark (, ; :) ends it.
CLAUSE_BREAK_MIN_DURATION_S: Final = 3.0
# Characters the first part needs when a full cue is cut back to its last punctuation mark.
BACKTRACK_MIN_CHARS: Final = 20
# Punctuation that ends a sentence.
SENTENCE_END_MARKS: Final = (".", "!", "?", "…")
# Punctuation that ends a clause.
CLAUSE_END_MARKS: Final = (",", ";", ":")
# Closing quotes and brackets skipped when looking for the punctuation at the end of a word.
CLOSING_CHARS: Final = "\"'\u201d\u2019\u00bb)]"

# Milliseconds in one second (SRT timestamps).
MS_PER_SECOND: Final = 1000
# Seconds in one hour (SRT timestamps).
SECONDS_PER_HOUR: Final = 3600
# Seconds in one minute (SRT timestamps).
SECONDS_PER_MINUTE: Final = 60

# Compression ratio above which Whisper itself treats a decoding as a repetition loop.
MAX_COMPRESSION_RATIO: Final = 2.4
# Characters of transcript text compressed at once (normal text compresses better the longer it is).
COMPRESSION_WINDOW_CHARS: Final = 500
# Shortest text whose compression ratio is judged.
MIN_COMPRESSION_CHARS: Final = 100
# Consecutive segments with the same text that mark a repetition loop.
MAX_SEGMENT_REPEATS: Final = 3
# Share of all segments above which one segment text dominates the transcript.
DOMINANT_SEGMENT_SHARE: Final = 0.5
# Fewest segments with the same text before that text can count as dominating.
DOMINANT_MIN_COUNT: Final = 3
# Longest word n-gram checked for repetition inside one segment.
MAX_NGRAM_WORDS: Final = 6
# Consecutive repetitions of one n-gram inside one segment that mark a repetition loop.
MAX_NGRAM_REPEATS: Final = 4
