"""Cancel and timeout through `FfmpegMediaTools.convert` with a real process, on every OS (plan m5 M5.4).

A runner wrapping `SubprocessRunner` swaps the ffmpeg command for a Python child that writes the
partial output forever, either itself or through a grandchild (like Chocolatey's ffmpeg shim,
which starts the real ffmpeg as its own child). The whole tree must die: the output folder ends
up empty and the writer's heartbeat stops.
"""

import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from support.watch import PartialWatcher
from transcribe_ui.api.errors import CancelledError, DomainError
from transcribe_ui.api.ports import CancelFlag, CancelToken
from transcribe_ui.media.adapter import FfmpegMediaTools, MediaTimeouts
from transcribe_ui.media.constants import ERROR_MEDIA_TOOL_TIMEOUT
from transcribe_ui.media.runner import ProcessResult, SubprocessRunner
from transcribe_ui.media.tools import ToolLocator

# Writer: appends to the partial output (argv[1]) and to a heartbeat file (argv[2]) forever.
WRITER = (
    "import sys, time\n"
    "with open(sys.argv[1], 'ab') as output:\n"
    "    while True:\n"
    "        output.write(b'audio')\n"
    "        output.flush()\n"
    "        with open(sys.argv[2], 'a') as beat:\n"
    "            beat.write('.')\n"
    "        time.sleep(0.01)\n"
)
# Shim: runs the writer as its own child and waits for it, as a package-manager shim does.
SHIM = "import subprocess, sys\nsubprocess.run([sys.executable, '-c', sys.argv[1], *sys.argv[2:]], check=False)\n"
# Conversion timeout of the timeout tests.
TIMEOUT_S = 2.0
# Longest the conversion may take to return after the kill (poll, kill, cleanup, CI slack).
KILL_BUDGET_S = 10.0
# How long a dead heartbeat must stay unchanged.
SILENCE_S = 0.3


@dataclass(frozen=True)
class WriterRunner:
    """Runs the writer (or the shim around it) instead of ffmpeg, with the real runner."""

    beat: Path
    shim: bool

    def run(self, argv: Sequence[str], timeout_s: float, cancel: CancelToken) -> ProcessResult:
        """Swap the command, keep the output path, run it for real."""
        output = argv[-1].removeprefix("file:")
        script = [SHIM, WRITER] if self.shim else [WRITER]
        child = [sys.executable, "-c", *script, output, str(self.beat)]
        return SubprocessRunner().run(child, timeout_s, cancel)


def _beats(beat: Path) -> int:
    return beat.stat().st_size if beat.exists() else 0


def _assert_stopped(beat: Path) -> None:
    beats = _beats(beat)
    time.sleep(SILENCE_S)
    assert _beats(beat) == beats, "a writer is still running"


def _tools(beat: Path, *, shim: bool, timeout_s: float = 3600.0) -> FfmpegMediaTools:
    locator = ToolLocator(which=lambda _tool: sys.executable)
    timeouts = MediaTimeouts(convert_s=timeout_s)
    return FfmpegMediaTools(locator=locator, runner=WriterRunner(beat, shim), timeouts=timeouts)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """Return a small source file (its size keeps the base timeout)."""
    path = tmp_path / "film.mkv"
    path.write_bytes(b"film")
    return path


@pytest.mark.parametrize("shim", [False, True])
def test_cancel_kills_the_tree_and_cleans_up(tmp_path: Path, source: Path, *, shim: bool) -> None:
    """Cancel while the tool writes: every process dies and no partial file stays."""
    output = tmp_path / "out"
    beat = tmp_path / "beat"
    flag = CancelFlag()
    watcher = PartialWatcher(output, flag)

    with pytest.raises(CancelledError):
        _tools(beat, shim=shim).convert(source, 0, output / "item", flag)

    watcher.join()
    assert watcher.written.is_set()
    assert not list(output.iterdir())
    _assert_stopped(beat)


@pytest.mark.parametrize("shim", [False, True])
def test_timeout_kills_the_tree_and_cleans_up(tmp_path: Path, source: Path, *, shim: bool) -> None:
    """A tool outliving its timeout is killed with what it started; no partial file stays."""
    output = tmp_path / "out"
    beat = tmp_path / "beat"
    started = time.monotonic()

    with pytest.raises(DomainError) as caught:
        _tools(beat, shim=shim, timeout_s=TIMEOUT_S).convert(source, 0, output / "item", CancelFlag())

    elapsed = time.monotonic() - started
    assert caught.value.code == ERROR_MEDIA_TOOL_TIMEOUT
    assert TIMEOUT_S <= elapsed < TIMEOUT_S + KILL_BUDGET_S
    assert not list(output.iterdir())
    _assert_stopped(beat)
