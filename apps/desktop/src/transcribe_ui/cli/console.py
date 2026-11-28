"""The CLI's streams and the prompt of `credentials`; the process's own unless a test injects them."""

import io
import sys
from dataclasses import dataclass, field
from typing import BinaryIO

from transcribe_ui.cli.prompt import Prompt, TerminalPrompt


def _process_stdin() -> BinaryIO:
    """Return this process's binary stdin; an empty stream when the process has none."""
    if sys.stdin is None:
        return io.BytesIO()
    return sys.stdin.buffer


def _process_stdout() -> BinaryIO:
    return sys.stdout.buffer


def _process_stderr() -> BinaryIO:
    return sys.stderr.buffer


@dataclass(frozen=True)
class Console:
    """Where the CLI reads the request (`run -`) and writes the response and progress; who it asks."""

    stdin: BinaryIO = field(default_factory=_process_stdin)
    stdout: BinaryIO = field(default_factory=_process_stdout)
    stderr: BinaryIO = field(default_factory=_process_stderr)
    # The prompt of `credentials`; None: the terminal (guidance on `stderr`).
    prompt: Prompt | None = None

    def active_prompt(self) -> Prompt:
        """Return the prompt of `credentials`."""
        if self.prompt is None:
            return TerminalPrompt.on_process(self.stderr)
        return self.prompt
