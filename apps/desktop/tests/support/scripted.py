"""A scripted process runner for media adapter tests: no tool runs, each call plays a step."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from transcribe_ui.api.errors import DomainError
from transcribe_ui.api.ports import CancelToken
from transcribe_ui.media.adapter import FfmpegMediaTools
from transcribe_ui.media.runner import ProcessResult
from transcribe_ui.media.tools import ToolLocator

# One scripted process: gets the argv, returns the result or raises.
type Step = Callable[[Sequence[str]], ProcessResult]

# What the scripted `which` finds on PATH.
PATH_TOOL = "/usr/bin/{tool}"


@dataclass
class ScriptedRunner:
    """`ProcessRunner` that plays one step per call and records each argv."""

    steps: list[Step]
    calls: list[list[str]] = field(default_factory=list)
    timeouts: list[float] = field(default_factory=list)

    def run(self, argv: Sequence[str], timeout_s: float, cancel: CancelToken) -> ProcessResult:
        """Play the next step."""
        assert (timeout_s > 0, cancel.is_cancelled()) == (True, False)
        self.calls.append(list(argv))
        self.timeouts.append(timeout_s)
        return self.steps.pop(0)(argv)


@dataclass(frozen=True)
class WritesThen:
    """Step that writes the output named last in argv (as ffmpeg starts to), then plays `after`."""

    after: Step

    def __call__(self, argv: Sequence[str]) -> ProcessResult:
        """Write the output, then play the next step."""
        Path(argv[-1].removeprefix("file:")).write_bytes(b"partial")
        return self.after(argv)


@dataclass(frozen=True)
class Raises:
    """Step that raises (a timeout, a cancel, an interrupt, a start failure)."""

    error: BaseException

    def __call__(self, _argv: Sequence[str]) -> ProcessResult:
        """Raise the error.

        Raises:
            BaseException: Always, the given error.
        """
        raise self.error


def exits(code: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> Step:
    """Return a step that ends with the exit code and outputs."""
    return lambda _argv: ProcessResult(code, stdout, stderr)


def which(tool: str) -> str:
    """Return what the scripted PATH holds, spelled as this OS spells paths."""
    return str(Path(PATH_TOOL.format(tool=tool)))


def scripted_tools(*steps: Step) -> tuple[FfmpegMediaTools, ScriptedRunner]:
    """Return the adapter on a runner playing the steps (tools found on the scripted PATH)."""
    runner = ScriptedRunner(list(steps))
    return FfmpegMediaTools(locator=ToolLocator(which=which), runner=runner), runner


def error_of(call: Callable[[], object]) -> DomainError:
    """Return the DomainError the call raises; fail the test if it raises none."""
    try:
        call()
    except DomainError as error:
        return error
    pytest.fail("no DomainError was raised")


def probe_starting_at(start: str) -> Step:
    """Return an ffprobe step reporting no streams and the container start time."""
    return exits(stdout=f'{{"streams": [], "format": {{"start_time": "{start}"}}}}'.encode())
