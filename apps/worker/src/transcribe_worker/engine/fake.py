"""Scripted fake Transcriber for flow tests and the image smoke test (no GPU, no models).

A file starting with `FAKE:` is a script: `say <text>`, `silence`, `fail`, `hang`, `crash`,
`break` (crash and leave the engine unable to start again), `quit` (answer, then exit while
idle), or `loop` (a repetition loop in
normal mode, `Recovered text.` in careful mode). Any other file (real audio) yields a fixed
sentence. The engine refuses to start while the file named by TRANSCRIBE_FAKE_BROKEN_FLAG exists.
"""

import os
import threading
import time
from pathlib import Path

from transcribe_contracts.classes import EngineInfo
from transcribe_contracts.constants import ALIGNMENT_LANGUAGES
from transcribe_worker.engine.classes import Segment, Transcript, Word
from transcribe_worker.engine.constants import (
    FAKE_BROKEN_FLAG_ENV,
    FAKE_CRASH_EXIT_CODE,
    FAKE_DEFAULT_TEXT,
    FAKE_HANG_S,
    FAKE_LOOP_COUNT,
    FAKE_LOOP_TEXT,
    FAKE_QUIT_DELAY_S,
    FAKE_SCRIPT_PREFIX,
    FAKE_WORD_S,
)


class FakeError(RuntimeError):
    """Failure requested by a `fail` script."""


class FakeTranscriber:
    """Transcriber that follows the script in the audio file."""

    def __init__(self) -> None:
        """Start, unless the broken flag file exists.

        Raises:
            FakeError: The broken flag file exists.
        """
        flag = os.environ.get(FAKE_BROKEN_FLAG_ENV)
        if flag and Path(flag).exists():
            msg = "scripted start failure"
            raise FakeError(msg)

    def settings(self, *, careful: bool) -> EngineInfo:
        """Return fixed fake settings."""
        return EngineInfo(
            name="fake",
            version="1",
            model="fake",
            compute_type="careful" if careful else "normal",
            batch_size=1,
        )

    def transcribe(self, audio: Path, language: str, *, careful: bool) -> Transcript:
        """Follow the file's script (see the module docstring)."""
        script = audio.read_bytes()
        if not script.startswith(FAKE_SCRIPT_PREFIX):
            return _speech(language, [FAKE_DEFAULT_TEXT])
        line = script.removeprefix(FAKE_SCRIPT_PREFIX).decode().strip()
        command, _, argument = line.partition(" ")
        _act(command)
        return _answer(command, argument, language, careful=careful)


def _act(command: str) -> None:
    """Carry out the side effect of a script: wait, die, or fail."""
    if command == "hang":
        time.sleep(FAKE_HANG_S)
    if command == "quit":
        threading.Timer(FAKE_QUIT_DELAY_S, os._exit, args=(FAKE_CRASH_EXIT_CODE,)).start()  # noqa: WPS437 - simulates an engine dying while idle
    if command == "break":
        Path(os.environ[FAKE_BROKEN_FLAG_ENV]).touch()
    if command in {"break", "crash"}:
        os._exit(FAKE_CRASH_EXIT_CODE)  # noqa: WPS437 - simulates a native crash on purpose
    if command == "fail":
        msg = "scripted failure"
        raise FakeError(msg)


def _answer(command: str, argument: str, language: str, *, careful: bool) -> Transcript:
    """Return the transcript a script asks for (none for silence and unknown scripts)."""
    if command == "say":
        return _speech(language, [argument])
    if command == "loop":
        return _speech(language, ["Recovered text."] if careful else _loop())
    if command == "quit":
        return _speech(language, [FAKE_DEFAULT_TEXT])
    return _speech(language, [])


def _speech(language: str, texts: list[str]) -> Transcript:
    aligned = language in ALIGNMENT_LANGUAGES
    segments: list[Segment] = []
    clock = float(0)
    for text in texts:
        segment = _segment(text, clock, aligned=aligned)
        segments.append(segment)
        clock = segment.end
    return Transcript(language=language, segments=tuple(segments), aligned=aligned)


def _segment(text: str, start: float, *, aligned: bool) -> Segment:
    tokens = text.split()
    words: list[Word] = []
    for index, token in enumerate(tokens):
        word_start = start + index * FAKE_WORD_S
        words.append(_word(token, word_start, aligned=aligned))
    end = start + len(tokens) * FAKE_WORD_S
    return Segment(start=start, end=end, text=text, words=tuple(words))


def _word(token: str, start: float, *, aligned: bool) -> Word:
    if not aligned:
        return Word(text=token)
    return Word(text=token, start=start, end=start + FAKE_WORD_S)


def _loop() -> list[str]:
    return [FAKE_LOOP_TEXT for _ in range(FAKE_LOOP_COUNT)]
