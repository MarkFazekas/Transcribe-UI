"""The prompt port of `credentials`: answers typed on the terminal, scripted in tests."""

import getpass
import io
import sys
from typing import BinaryIO, Protocol, Self, TextIO

from transcribe_ui.cli.constants import END_OF_INPUT_ANSWERS, LINE_END


class Prompt(Protocol):
    """Asks the user for values, with or without showing what they type."""

    def interactive(self) -> bool:
        """Return whether a person can answer (stdin is a terminal)."""
        ...

    def tell(self, text: str) -> None:
        """Show a line of guidance (never a value the user typed)."""
        ...

    def ask(self, label: str) -> str:
        """Show the label and return the answer; the typed characters are echoed.

        Raises:
            KeyboardInterrupt: The user pressed Ctrl+C.
            EOFError: The input ended (Ctrl+D, Ctrl+Z) before a line.
        """
        ...

    def ask_hidden(self, label: str) -> str:
        """Show the label and return the answer; the typed characters are not echoed.

        Raises:
            KeyboardInterrupt: The user pressed Ctrl+C.
            EOFError: The input ended (Ctrl+D, Ctrl+Z) before a line.
        """
        ...


def _checked(answer: str) -> str:
    """Return the answer, or raise EOFError for the end-of-input character Windows hands over.

    Raises:
        EOFError: The answer is exactly Ctrl+Z or Ctrl+D (Windows `getpass` returns it as text).
    """
    if answer in END_OF_INPUT_ANSWERS:
        raise EOFError
    return answer


class TerminalPrompt:
    """The prompt on the terminal: labels and guidance on stderr, hidden answers through `getpass`.

    Nothing goes to stdout, which carries only the JSON response.
    """

    def __init__(self, stderr: BinaryIO, lines: TextIO, *, stdin_is_terminal: bool) -> None:
        """Write to `stderr`, read echoed answers from `lines` (stdin); ask only when it is a terminal."""
        self._interactive = stdin_is_terminal
        self._stderr = stderr
        self._lines = lines

    @classmethod
    def on_process(cls, stderr: BinaryIO) -> Self:
        """Return the prompt on this process's stdin."""
        if sys.stdin is None:
            return cls(stderr, io.StringIO(), stdin_is_terminal=False)
        return cls(stderr, sys.stdin, stdin_is_terminal=sys.stdin.isatty())

    def interactive(self) -> bool:
        """Return whether stdin is a terminal."""
        return self._interactive

    def tell(self, text: str) -> None:
        """Write the line to stderr."""
        self._write(text.encode("utf-8") + LINE_END)

    def ask(self, label: str) -> str:
        """Write the label to stderr and read one line from stdin (the terminal echoes it).

        Raises:
            EOFError: stdin ended before a line.
        """
        self._write(f"{label}: ".encode())
        line = self._lines.readline()
        if not line:
            raise EOFError
        return _checked(line.removesuffix("\n").removesuffix("\r"))

    def ask_hidden(self, label: str) -> str:
        """Ask with `getpass`, which reads from the terminal without echo."""
        return _checked(getpass.getpass(f"{label}: "))

    def _write(self, raw: bytes) -> None:
        self._stderr.write(raw)
        self._stderr.flush()
