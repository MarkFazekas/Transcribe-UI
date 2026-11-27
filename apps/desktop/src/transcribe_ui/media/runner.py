"""Running ffmpeg/ffprobe: a port, so most tests use a fake, and its subprocess adapter.

Every process starts in its own process group (a new session on POSIX,
`CREATE_NEW_PROCESS_GROUP` on Windows), so a Ctrl+C in the terminal reaches only the app, whose
first Ctrl+C sets the cancel token. The adapter kills the process itself when the token is set,
when the timeout passes, or when anything else interrupts the wait (a second Ctrl+C).
"""

import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from transcribe_ui.api.errors import CancelledError
from transcribe_ui.api.ports import CancelToken
from transcribe_ui.media.constants import KILL_WAIT_S, POLL_INTERVAL_S

logger = logging.getLogger(__name__)

# What a finished process wrote: stdout, stderr.
type CapturedOutput = tuple[bytes, bytes]


@dataclass(frozen=True)
class ProcessResult:
    """How a process ended and what it wrote."""

    returncode: int
    stdout: bytes
    stderr: bytes


class ProcessTimeoutError(Exception):
    """The process outlived its timeout and was killed."""


class ProcessRunner(Protocol):
    """Runs one external process to its end."""

    def run(self, argv: Sequence[str], timeout_s: float, cancel: CancelToken) -> ProcessResult:
        """Run the process with no stdin, capturing stdout and stderr.

        A process still running when the token is set or the timeout passes is killed, and
        it has exited when this returns or raises.

        Raises:
            CancelledError: The cancel token was set (the process was not started or was killed).
            ProcessTimeoutError: The process outlived `timeout_s` and was killed.
            OSError: The process could not be started.
        """
        ...


class SubprocessRunner:
    """`ProcessRunner` on `subprocess.Popen`, for Windows and POSIX."""

    def __init__(self, poll_interval_s: float = POLL_INTERVAL_S, kill_wait_s: float = KILL_WAIT_S) -> None:
        """Check the token and the deadline every `poll_interval_s`; wait `kill_wait_s` for a killed process."""
        self._poll_interval_s = poll_interval_s
        self._kill_wait_s = kill_wait_s

    def run(self, argv: Sequence[str], timeout_s: float, cancel: CancelToken) -> ProcessResult:
        """Run the process; see `ProcessRunner.run`.

        Raises:
            CancelledError: The cancel token was set (the process was not started or was killed).
        """
        if cancel.is_cancelled():
            raise CancelledError
        deadline = time.monotonic() + timeout_s
        process = _start(argv)
        try:
            return self._wait(process, deadline, cancel)
        except BaseException:
            _kill(process, self._kill_wait_s)
            raise

    def _wait(self, process: subprocess.Popen[bytes], deadline: float, cancel: CancelToken) -> ProcessResult:
        while True:
            if cancel.is_cancelled():
                raise CancelledError
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProcessTimeoutError
            output = _communicate(process, min(self._poll_interval_s, remaining))
            if output is not None:
                return ProcessResult(process.returncode, *output)


def _communicate(process: subprocess.Popen[bytes], timeout_s: float) -> CapturedOutput | None:
    """Return stdout and stderr once the process has exited, or None if it is still running."""
    try:
        return process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        # Retrying `communicate` loses no output (documented), so the caller polls again.
        return None


def _start(argv: Sequence[str]) -> subprocess.Popen[bytes]:
    # A fixed argument list, never a shell: no user text is interpreted by a shell.
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        return subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=flags,
        )
    return subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def _kill(process: subprocess.Popen[bytes], wait_s: float) -> None:
    """Kill the process (its whole group on POSIX) and wait for it to exit."""
    if process.poll() is None:
        _signal_kill(process)
    try:
        process.communicate(timeout=wait_s)
    except subprocess.TimeoutExpired:
        logger.warning("Process %s did not exit within %s s of being killed", process.pid, wait_s)


def _signal_kill(process: subprocess.Popen[bytes]) -> None:
    if sys.platform == "win32":
        process.kill()
        return
    try:
        # The process leads its own session, so its group id is its pid.
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        logger.debug("Process %s was already gone", process.pid)
