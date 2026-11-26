"""Regression tests of the subtitle cue builder, the SRT writer and the transcript quality checks.

Times are written as strings ("start end", "word start end") to keep the tables readable.
"""

import itertools
import unicodedata

import pytest
from pydantic import ValidationError

from transcribe_worker.engine.classes import Segment, Transcript, Word
from transcribe_worker.subtitles import srt
from transcribe_worker.subtitles.classes import Cue
from transcribe_worker.subtitles.constants import COMPRESSION_WINDOW_CHARS, MAX_CUE_DURATION_S, MAX_LINE_CHARS
from transcribe_worker.subtitles.cues import build_cues
from transcribe_worker.subtitles.layout import split_lines
from transcribe_worker.subtitles.quality import clean, find_problems

HUNGARIAN = "hu"
ENGLISH = "en"
GERMAN = "de"
JAPANESE = "ja"
CHINESE = "zh"
HUNGARIAN_PARAGRAPH = (
    "Jó napot kívánok, a mai adásunkban a magyar nyelv történetéről lesz szó. Először a honfoglalás előtti "
    "időszakot tekintjük át, majd rátérünk a középkori nyelvemlékekre, például a Halotti beszédre és "
    "könyörgésre. Végül megnézzük, hogyan változott a helyesírás a 2024. november végéig."
)
ENGLISH_PARAGRAPH = (
    "Good evening, and welcome to the program. Tonight we are going to talk about the history of the English "
    "language, from the Anglo-Saxon period to the present day. It is a long story, but I promise it will be "
    "worth it."
)
ENGLISH_MORE = (
    "The first speakers arrived from the continent in the fifth century, bringing dialects that slowly merged. "
    "Later the Vikings and then the Normans left their mark on the vocabulary, which is why so many everyday "
    "words have two forms today: one plain and one more formal. Spelling was fixed much later, when printing "
    "made books cheap, and that is the reason it often looks so strange to learners."
)
# Fullwidth punctuation (ruff's ambiguous-character check) is how Japanese and Chinese are written, so it stays literal here.
JAPANESE_PARAGRAPH = (
    "今日はとても良い天気ですね。散歩に行きましょうか、それとも家で映画を見ますか？"  # noqa: RUF001 - see above
    "私は公園へ行って、桜の写真をたくさん撮りたいと思います。「iPhone」で撮った写真も見せますね。"
)
CHINESE_PARAGRAPH = (
    "今天天气很好，我们一起去公园散步吧。你想先去看花，还是先去湖边喝咖啡？"  # noqa: RUF001 - see above
    "我觉得两个都不错，不过我们得早点出发，因为下午可能会下雨。"  # noqa: RUF001 - see above
)
UNSPACED_PUNCTUATION = "。、，？「」"  # noqa: RUF001 - see above
CHARACTER_SECONDS = 0.25
STUCK_COPIES = 3
LONG_WORD = "megszentségteleníthetetlenségeskedéseitekért"
THANKS = "Köszönöm, hogy megnézték!"
THANK_YOU = "Köszönöm szépen."
GOODBYE = "Viszlát."
EMPTY = "empty"
WORD_SPACING = 0.1
SEGMENT_SECONDS = 2.0
HALLUCINATION_REPEATS = 8


def span(times: str) -> tuple[float, float]:
    """Return the two times of "start end"."""
    start, end = map(float, times.split())
    return start, end


def word(spec: str) -> Word:
    """Return a word from "text start end", or an untimed word from "text"."""
    text, *times = spec.split()
    if not times:
        return Word(text=text)
    start, end = span(" ".join(times))
    return Word(text=text, start=start, end=end)


def aligned(times: str, *words: str, text: str = "") -> Segment:
    """Return a segment over "start end" with the given word specs."""
    start, end = span(times)
    return Segment(start=start, end=end, text=text, words=tuple(map(word, words)))


def untimed(times: str, text: str) -> Segment:
    """Return a segment over "start end" without words."""
    start, end = span(times)
    return Segment(start=start, end=end, text=text)


def spoken(text: str, times: str) -> Segment:
    """Return a segment whose words get times like an aligner's: length-proportional, with small gaps."""
    start, end = span(times)
    parts = text.split()
    letters = sum(map(len, parts))
    gap = (end - start) * WORD_SPACING / len(parts)
    words = []
    cursor = start
    for part in parts:
        duration = (end - start) * (1 - WORD_SPACING) * len(part) / letters
        words.append(Word(text=part, start=round(cursor, 3), end=round(cursor + duration, 3)))
        cursor += duration + gap
    return Segment(start=start, end=end, text=text, words=tuple(words))


def per_character(text: str, start: float) -> Segment:
    """Return a segment with one word per character, like WhisperX's alignment of unspaced scripts.

    Characters get CHARACTER_SECONDS each; punctuation stays untimed, as the aligner leaves it.
    """
    words = [
        Word(text=char)
        if char in UNSPACED_PUNCTUATION
        else Word(text=char, start=start + index * CHARACTER_SECONDS, end=start + (index + 1) * CHARACTER_SECONDS)
        for index, char in enumerate(text)
    ]
    return Segment(start=start, end=start + len(text) * CHARACTER_SECONDS, text=text, words=tuple(words))


def transcript(*segments: Segment, language: str = HUNGARIAN) -> Transcript:
    """Return an aligned transcript of the segments."""
    return Transcript(language=language, segments=segments, aligned=True)


def plain(*texts: str) -> Transcript:
    """Return a transcript with one untimed segment per text, two seconds each."""
    segments = (
        Segment(start=index * SEGMENT_SECONDS, end=(index + 1) * SEGMENT_SECONDS, text=text)
        for index, text in enumerate(texts)
    )
    return transcript(*segments)


def cue(times: str, *lines: str) -> Cue:
    """Return a cue over "start end"."""
    start, end = span(times)
    return Cue(start=start, end=end, lines=lines)


def describe(cues: tuple[Cue, ...]) -> list[str]:
    """Return each cue as "start end line | line" with millisecond times."""
    return [f"{subtitle.start:.3f} {subtitle.end:.3f} {' | '.join(subtitle.lines)}" for subtitle in cues]


def assert_valid(cues: tuple[Cue, ...], text: str) -> None:
    """Check the rules every cue list must keep, including that no text of `text` is lost."""
    for subtitle in cues:
        assert all(len(line) <= MAX_LINE_CHARS or " " not in line for line in subtitle.lines)
        assert subtitle.end - subtitle.start <= MAX_CUE_DURATION_S
    for current, following in itertools.pairwise(cues):
        assert current.end <= following.start
    assert " ".join(line for subtitle in cues for line in subtitle.lines) == " ".join(text.split())


def assert_valid_unspaced(cues: tuple[Cue, ...], text: str) -> None:
    """Check the cue rules for text without spaces: no space inserted, no text lost, limits kept."""
    lines = [line for subtitle in cues for line in subtitle.lines]
    assert all(len(line) <= MAX_LINE_CHARS for line in lines)
    assert all(subtitle.end - subtitle.start <= MAX_CUE_DURATION_S for subtitle in cues)
    for current, following in itertools.pairwise(cues):
        assert current.end <= following.start
    assert "".join(lines) == text


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            transcript(spoken(HUNGARIAN_PARAGRAPH, "3 25")),
            [
                "3.000 8.575 Jó napot kívánok, a mai adásunkban a | magyar nyelv történetéről lesz szó.",
                "8.630 12.709 Először a honfoglalás előtti | időszakot tekintjük át,",
                "12.764 15.998 majd rátérünk a középkori nyelvemlékekre,",
                "16.053 19.343 például a Halotti beszédre és könyörgésre.",
                "19.398 24.945 Végül megnézzük, hogyan változott a | helyesírás a 2024. november végéig.",
            ],
            id="hungarian",
        ),
        pytest.param(
            transcript(spoken(ENGLISH_PARAGRAPH, "3 17"), language=ENGLISH),
            [
                "3.000 5.711 Good evening, and welcome to the program.",
                "5.745 10.378 Tonight we are going to talk about | the history of the English language,",
                "10.412 13.515 from the Anglo-Saxon | period to the present day.",
                "13.549 16.966 It is a long story, but I | promise it will be worth it.",
            ],
            id="english",
        ),
    ],
)
def test_paragraph_cues(source: Transcript, expected: list[str]) -> None:
    """Sentence ends and long clauses end cues; "2024. november" (ordinal, lower case next) does not."""
    cues = build_cues(source)

    assert describe(cues) == expected
    assert_valid(cues, source.text)


def test_segment_without_word_times() -> None:
    """A segment without words gets times spread over its characters, still within limits."""
    source = transcript(untimed("10 40", f"{HUNGARIAN_PARAGRAPH} {ENGLISH_PARAGRAPH}"))

    cues = build_cues(source)

    assert len(cues) > 1
    assert (cues[0].start, cues[-1].end) == span("10 40")
    assert_valid(cues, source.text)


def test_untimed_words_between_timed_ones() -> None:
    """Words the aligner missed share the gap between their timed neighbours or the segment edges."""
    source = transcript(aligned("0.5 4", "Ez", "egy 1.0 1.2", "nagyon", "hosszú 2.0 2.4", "mondat."))

    cues = build_cues(source)

    assert describe(cues) == ["0.500 4.000 Ez egy nagyon hosszú mondat."]


def test_pause_splits_and_short_cue_is_extended() -> None:
    """A pause over 0.6 s ends a cue; a short cue grows to 1 s but never into the next one."""
    source = transcript(
        aligned("1 5.6", "Igen 1.0 1.3", "persze 2.0 2.4", "értem. 2.45 2.9", "És 5.0 5.2", "aztán? 5.25 5.6"),
    )

    cues = build_cues(source)

    assert describe(cues) == ["1.000 2.000 Igen", "2.000 3.000 persze értem.", "5.000 6.000 És aztán?"]


def test_word_longer_than_a_line_keeps_its_own_line() -> None:
    """A word over 42 characters is neither cut nor dropped and shares no line."""
    text = f"A szó: {LONG_WORD} és még néhány szó a végén, hogy kitöltse."
    source = transcript(spoken(text, "0 6"))

    cues = build_cues(source)

    assert [subtitle.lines for subtitle in cues] == [
        ("A szó:", LONG_WORD),
        ("és még néhány szó a végén, hogy kitöltse.",),
    ]
    assert_valid(cues, source.text)


def test_broken_engine_times() -> None:
    """Overlapping, reversed and zero-length word times still give sorted, non-overlapping cues."""
    stuck = tuple(f"{part} 5 5" for part in ENGLISH_PARAGRAPH.split())
    source = transcript(
        aligned("1 2", "Egy 1.0 1.0", "kettő 0.9 1.5", "három. 1.5 1.2"),
        untimed("1.2 1.4", "  Négy \n öt  "),
        untimed("4 4", "   "),
        aligned("5 5", *stuck),
    )

    cues = build_cues(source)

    assert describe(cues) == [
        "1.000 2.000 Egy kettő három. Négy öt",
        "5.000 5.100 Good evening, and welcome to the program.",
        "5.100 5.200 Tonight we are going to talk about | the history of the English language,",
        "5.200 5.300 from the Anglo-Saxon period to the | present day. It is a long story,",
        "5.300 6.300 but I promise it will be worth it.",
    ]
    assert_valid(cues, f"Egy kettő három. Négy öt {ENGLISH_PARAGRAPH}")


@pytest.mark.parametrize(
    ("text", "language"),
    [
        pytest.param(JAPANESE_PARAGRAPH, JAPANESE, id="japanese"),
        pytest.param(CHINESE_PARAGRAPH, CHINESE, id="chinese"),
    ],
)
def test_unspaced_aligned_characters(text: str, language: str) -> None:
    """Per-character words are joined without spaces; cues end at sentence marks, lines break inside the text."""
    cues = build_cues(transcript(per_character(text, 2), language=language))

    assert len(cues) > 1
    assert all(subtitle.lines[-1][-1] in UNSPACED_PUNCTUATION for subtitle in cues)
    assert_valid_unspaced(cues, text)


@pytest.mark.parametrize(
    ("text", "language"),
    [
        pytest.param(JAPANESE_PARAGRAPH, JAPANESE, id="japanese"),
        pytest.param(CHINESE_PARAGRAPH, CHINESE, id="chinese"),
    ],
)
def test_unspaced_segment_without_word_times(text: str, language: str) -> None:
    """A long unspaced segment is split into units with spread times instead of one overlong cue."""
    cues = build_cues(transcript(untimed("10 35", text), language=language))

    assert len(cues) > 1
    assert (cues[0].start, cues[-1].end) == span("10 35")
    assert_valid_unspaced(cues, text)


def test_unspaced_segments_join_after_punctuation() -> None:
    """A Japanese segment after one ending in "、" follows it without a space in the same cue."""
    segments = (untimed("0 1", "今日は、"), untimed("1 2.5", "散歩しよう。"))

    assert describe(build_cues(transcript(*segments, language=JAPANESE))) == ["0.000 2.500 今日は、散歩しよう。"]


def test_unspaced_text_keeps_its_own_spaces() -> None:
    """Spaces the engine wrote in unspaced text (Thai phrase breaks) are kept, none are added."""
    text = "สวัสดีครับ วันนี้อากาศดีมาก"

    cues = build_cues(transcript(untimed("0 3", text), language="th"))

    assert [subtitle.lines for subtitle in cues] == [(text,)]


def test_equal_time_words_do_not_delay_later_cues() -> None:
    """Many words stuck at one time share it and later cues stay in sync (delay at most 0.5 s, here none)."""
    stuck_text = " ".join(itertools.repeat(f"{ENGLISH_PARAGRAPH} {ENGLISH_MORE}", STUCK_COPIES))
    stuck = aligned("5 5", *(f"{part} 5 5" for part in stuck_text.split()))

    cues = build_cues(transcript(stuck, spoken(HUNGARIAN_PARAGRAPH, "6 28")))

    following = [subtitle for subtitle in cues if subtitle.lines[0].startswith("Jó napot")]
    assert [subtitle.start for subtitle in following] == [span("6 28")[0]]
    assert_valid(cues, f"{stuck_text} {HUNGARIAN_PARAGRAPH}")


@pytest.mark.parametrize(
    ("text", "language"),
    [
        pytest.param("Yesterday I met J. Smith from the U.S. Army there.", ENGLISH, id="initial-and-acronym"),
        pytest.param("Wir treffen uns am 3. Februar in Berlin.", GERMAN, id="ordinal"),
    ],
)
def test_abbreviation_does_not_end_a_cue(text: str, language: str) -> None:
    """A dot after an initial, a dotted acronym or an ordinal is no sentence end, even before a capital."""
    cues = build_cues(transcript(spoken(text, "0 5"), language=language))

    assert [subtitle.lines for subtitle in cues] == [tuple(split_lines(text) or ())]


def test_year_ends_a_sentence() -> None:
    """A dotted number of three or more digits (a year) still ends a sentence before a capital."""
    text = "It all happened in 2023. Then we moved to Berlin."

    cues = build_cues(transcript(spoken(text, "0 6"), language=ENGLISH))

    assert [subtitle.lines for subtitle in cues] == [("It all happened in 2023.",), ("Then we moved to Berlin.",)]


def test_empty_transcript() -> None:
    """No segments, or only blank text, give no cues and an empty SRT."""
    assert build_cues(transcript()) == ()
    assert build_cues(plain("", "  ")) == ()
    assert not srt.to_srt(())


def test_golden_srt() -> None:
    """A small transcript renders exactly as expected."""
    words = ("Hello 0.5 0.8", "there. 0.85 1.2", "How 1.6 1.75", "are 1.8 1.9", "you? 1.95 2.3")
    source = transcript(
        aligned("0.5 2.9", *words, text="Hello there. How are you?"),
        untimed("5 6", "  I am   fine, thanks. "),
        untimed("6.5 10", "This line is long enough to need two lines on the screen."),
        language=ENGLISH,
    )

    assert srt.to_srt(build_cues(source)) == (
        "1\n00:00:00,500 --> 00:00:02,300\nHello there. How are you?\n\n"
        "2\n00:00:05,000 --> 00:00:06,000\nI am fine, thanks.\n\n"
        "3\n00:00:06,500 --> 00:00:10,000\nThis line is long enough to\nneed two lines on the screen.\n"
    )


def test_srt_millisecond_rounding() -> None:
    """Rounding never empties a cue nor makes it overlap the previous one; hours roll over."""
    cues = (cue("0.0004 0.0006", "a"), cue("0.0006 0.0011", "b"), cue("3599.9996 3600.0004", "c"))

    assert srt.to_srt(cues) == (
        "1\n00:00:00,000 --> 00:00:00,001\na\n\n"
        "2\n00:00:00,001 --> 00:00:00,002\nb\n\n"
        "3\n01:00:00,000 --> 01:00:00,001\nc\n"
    )


@pytest.mark.parametrize(
    ("times", "lines"),
    [
        pytest.param("1 1", ("a",), id="zero-length"),
        pytest.param("1 2", (), id="no-line"),
        pytest.param("1 2", ("a", "b", "c"), id="three-lines"),
        pytest.param("1 2", ("a", " "), id="blank-line"),
        pytest.param("1 2", ("a\nb",), id="line-break"),
    ],
)
def test_invalid_cue(times: str, lines: tuple[str, ...]) -> None:
    """A cue refuses times and lines that would break an SRT file."""
    with pytest.raises(ValidationError):
        cue(times, *lines)


def test_normal_speech_has_no_problems() -> None:
    """Ordinary Hungarian and English speech passes, also when long enough for the compression check."""
    paragraphs = (HUNGARIAN_PARAGRAPH, ENGLISH_PARAGRAPH, ENGLISH_MORE)
    sentences = [sentence for paragraph in paragraphs for sentence in paragraph.split(". ")]
    source = plain(*sentences, THANK_YOU, GOODBYE)

    assert len(source.text) > COMPRESSION_WINDOW_CHARS
    assert find_problems(source) == ()
    assert clean(source) == source


@pytest.mark.parametrize(
    ("source", "reasons"),
    [
        pytest.param(transcript(), (EMPTY,), id="no-segments"),
        pytest.param(plain(" ", ""), (EMPTY,), id="blank-segments"),
        pytest.param(
            plain("Szia!", THANK_YOU, THANK_YOU, "köszönöm  szépen", GOODBYE), ("in a row",), id="segment-loop"
        ),
        pytest.param(plain(THANKS, "Sziasztok!", THANKS, GOODBYE, THANKS), ("makes up",), id="dominant-segment"),
        pytest.param(
            plain("Jó estét kívánok.", " ".join(itertools.repeat(THANKS, HALLUCINATION_REPEATS))),
            ("compresses", "repeats 'köszönöm hogy megnézték'"),
            id="hallucinated-phrase",
        ),
        pytest.param(plain("and then and then and then and then we left"), ("repeats 'and then'",), id="ngram-loop"),
        pytest.param(plain("नमस्ते नमस्ते नमस्ते नमस्ते दोस्तों"), ("repeats 'नमस्ते'",), id="combining-marks"),
        pytest.param(
            plain(unicodedata.normalize("NFD", THANK_YOU), THANK_YOU, THANK_YOU), ("in a row",), id="decomposed-loop"
        ),
    ],
)
def test_problems(source: Transcript, reasons: tuple[str, ...]) -> None:
    """Typical Whisper failures are flagged, each with a readable reason."""
    problems = find_problems(source)

    for reason in reasons:
        assert any(reason in problem for problem in problems), problems


def test_clean() -> None:
    """Blank segments are dropped; a loop of three or more duplicates (case and punctuation ignored) keeps one."""
    source = plain(THANK_YOU, "  ", "köszönöm szépen!", "Köszönöm  szépen", GOODBYE, THANK_YOU)

    cleaned = clean(source)

    assert [segment.text for segment in cleaned.segments] == [THANK_YOU, GOODBYE, THANK_YOU]
    assert cleaned.language == source.language


def test_repeated_sentence_is_kept() -> None:
    """Regression: a sentence said twice (two aligned segments) is speech, not a loop, and keeps both cues' text."""
    source = transcript(
        spoken("Come on.", "1 1.6"),
        spoken("Come on.", "1.7 2.3"),
        spoken("We are late.", "2.4 3.5"),
        language=ENGLISH,
    )

    cleaned = clean(source)

    assert cleaned == source
    assert describe(build_cues(cleaned)) == ["1.000 2.270 Come on. Come on.", "2.400 3.463 We are late."]
