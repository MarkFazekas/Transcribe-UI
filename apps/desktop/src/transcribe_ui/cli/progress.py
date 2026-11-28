"""Progress output of `run`: one line per event on stderr, NDJSON (default) or human-readable text."""

import threading
from collections.abc import Callable
from typing import BinaryIO

from transcribe_ui.api.classes import ProgressEvent
from transcribe_ui.api.ports import ProgressSink
from transcribe_ui.cli.constants import LINE_END, TEXT_PROGRESS_PATH_SEPARATOR, ProgressFormat
from transcribe_ui.i18n.texts import progress_text

# Renders one progress event as the bytes of its line, without the line end.
type EventLine = Callable[[ProgressEvent], bytes]


def ndjson_line(event: ProgressEvent) -> bytes:
    """Return the event as one JSON line (the machine contract; never translated)."""
    return event.model_dump_json().encode("utf-8")


def _shown_character(character: str) -> str:
    if character.isprintable():
        return character
    return character.encode("unicode_escape").decode("ascii")


def printable(text: str) -> str:
    """Return the text with each character that is not printable escaped as Python escapes it.

    A path may hold a line break, a terminal escape or (POSIX) an undecodable byte; escaped, it
    stays on its line, cannot style the terminal and encodes as UTF-8.
    """
    return "".join(map(_shown_character, text))


def text_line(event: ProgressEvent) -> str:
    """Return the event as "stage completed/total: path" in the active language.

    The text of the GUI's activity panel (`progress_text`), then the path with its characters
    that are not printable escaped.
    """
    shown = progress_text(event)
    if event.path is None:
        return shown
    return f"{shown}{TEXT_PROGRESS_PATH_SEPARATOR}{printable(event.path)}"


def _text_bytes(event: ProgressEvent) -> bytes:
    return text_line(event).encode("utf-8")


class LineProgress:
    """Progress sink writing one line per event; safe to call from several threads."""

    def __init__(self, stream: BinaryIO, render: EventLine) -> None:
        """Write to this binary stream, each event rendered by `render`."""
        self._stream = stream
        self._render = render
        self._lock = threading.Lock()

    def emit(self, event: ProgressEvent) -> None:
        """Write the event as one line and flush."""
        line = self._render(event) + LINE_END
        with self._lock:
            self._stream.write(line)
            self._stream.flush()


def progress_sink(progress_format: ProgressFormat, stream: BinaryIO) -> ProgressSink:
    """Return the sink writing events to `stream` in this format."""
    render = ndjson_line if progress_format == "ndjson" else _text_bytes
    return LineProgress(stream, render)
