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
# Longest delay of a cue behind its words caused by such moves; a pile of cues beyond it shares the time.
MAX_CUE_DELAY_S: Final = 0.5
# Silence between two words (seconds) that always starts a new cue.
PAUSE_BREAK_S: Final = 0.6
# Characters a cue needs before a clause mark (, ; :) ends it.
CLAUSE_BREAK_MIN_CHARS: Final = 30
# Seconds a cue needs before a clause mark (, ; :) ends it.
CLAUSE_BREAK_MIN_DURATION_S: Final = 3.0
# Characters the first part needs when a full cue is cut back to its last punctuation mark.
BACKTRACK_MIN_CHARS: Final = 20
# Punctuation that ends a sentence (also the CJK, Myanmar, Tibetan and Khmer marks).
# Fullwidth punctuation is written as escapes throughout, since it looks like ASCII.
SENTENCE_END_MARKS: Final = tuple(".!?…。\uff01\uff1f\uff0e။།។")
# Punctuation that ends a clause (also the CJK and Myanmar marks).
CLAUSE_END_MARKS: Final = tuple(",;:、\uff0c\uff1b\uff1a၊")
# Closing quotes and brackets skipped when looking for the punctuation at the end of a word.
CLOSING_CHARS: Final = "\"'\u201d\u2019\u00bb)]」』\uff09】\u3015》〉"
# Code point ranges of scripts written without spaces between words: Thai, Lao, Tibetan, Myanmar,
# Khmer, and the CJK blocks (punctuation, kana, ideographs, fullwidth forms); Hangul is spaced.
UNSPACED_RANGES: Final = (
    (0xE00, 0xFFF),
    (0x1000, 0x109F),
    (0x1780, 0x17FF),
    (0x19E0, 0x19FF),
    (0x2E80, 0x2FDF),
    (0x3000, 0x30FF),
    (0x3100, 0x312F),
    (0x3190, 0x31FF),
    (0x3200, 0x33FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xA9E0, 0xA9FF),
    (0xAA60, 0xAA7F),
    (0xF900, 0xFAFF),
    (0xFE30, 0xFE4F),
    (0xFF00, 0xFFEF),
    (0x1B000, 0x1B16F),
    (0x20000, 0x3FFFF),
)
# Characters that never start a line or a unit in unspaced text (closing marks, small kana, iteration
# marks, Thai/Lao following vowels).
NO_BREAK_BEFORE: Final = (
    ".,!?;:)]}%…‥・。、\uff0c\uff0e\uff01\uff1f\uff1b\uff1a」』\uff09\uff3d\uff5d\u3015〉》】〗〙〟\u201d\u2019ー々ゝゞヽヾ"
    "ぁぃぅぇぉっゃゅょゎゕゖァィゥェォッャュョヮヵヶะาำๆະາຳໆ။၊།។៕"
)
# Characters that never end a line or a unit in unspaced text (opening marks, Thai/Lao leading vowels, Khmer
# coeng and Myanmar virama).
NO_BREAK_AFTER: Final = "([{「『\uff08\uff3b\uff5b\u3014〈《【〖〘〝\u201c\u2018เแโใไເແໂໃໄ\u17d2\u1039"
# Unicode general category prefix of combining marks, which stay with the character before them.
MARK_CATEGORY_PREFIX: Final = "M"
# Unicode general category prefix of punctuation; segments after unspaced punctuation join without a space.
PUNCTUATION_CATEGORY_PREFIX: Final = "P"
# Most digits of an ordinal written with a dot ("3." as a day of the month); longer numbers (years) may end a sentence.
MAX_ORDINAL_DIGITS: Final = 2
# Unicode general categories of combining marks kept in comparison keys (non-spacing and spacing).
KEPT_MARK_CATEGORIES: Final = frozenset(("Mn", "Mc"))

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
