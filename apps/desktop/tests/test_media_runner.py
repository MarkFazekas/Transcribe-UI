"""The subprocess runner: output capture, timeout, cancel and interrupt kill the child (plan m5 M5.4).

The children are small Python scripts, so the tests run the same on Windows and POSIX. A
"heartbeat" child appends to a file every few milliseconds; once the runner returns, the file
must stop growing, which shows the process is gone without OS-specific process lookups.
"""

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from transcribe_ui.api.errors import CancelledError
from transcribe_ui.api.ports import CancelFlag, CancelToken
from transcribe_ui.media.runner import ProcessTimeoutError, SubprocessRunner, kill_running_tools, running_tools

# Child that appends a dot to the file named by its argument every 10 ms, forever.
HEARTBEAT = "import sys, time\nwhile True:\n    with open(sys.argv[1], 'a') as beat:\n        beat.write('.')\n    time.sleep(0.01)\n"
# Child that writes a lot to both pipes (more than any pipe buffer), then exits 3.
CHATTY = "import sys\nsys.stdout.write('o' * 1_000_000)\nsys.stderr.write('e' * 1_000_000)\nsys.exit(3)\n"
# Generous timeout of children that end by themselves.
RUN_TIMEOUT_S = 60.0
# Longest the runner may take to return after a kill (poll interval + kill, with CI slack).
KILL_BUDGET_S = 5.0
# Longest to wait for a heartbeat child to start beating.
START_WAIT_S = 20.0
# How often to look for the heartbeat file.
WATCH_INTERVAL_S = 0.01
# How long a dead heartbeat must stay unchanged.
SILENCE_S = 0.3


def _python(*arguments: str) -> list[str]:
    return [sys.executable, "-c", *arguments]


def _wait_for_beat(beat: Path) -> None:
    deadline = time.monotonic() + START_WAIT_S
    while not beat.exists() and time.monotonic() < deadline:
        time.sleep(WATCH_INTERVAL_S)
    assert beat.exists(), "the child never started"


def _cancel_once_beating(beat: Path, flag: CancelFlag) -> None:
    _wait_for_beat(beat)
    flag.cancel()


def _beats(beat: Path) -> int:
    return beat.stat().st_size if beat.exists() else 0


def _assert_stopped(beat: Path) -> None:
    beats = _beats(beat)
    time.sleep(SILENCE_S)
    assert _beats(beat) == beats, "the child is still running"


class CancelWhenBeating:
    """Cancel token that becomes set once the heartbeat file exists."""

    def __init__(self, beat: Path) -> None:
        """Watch this heartbeat file."""
        self._beat = beat

    def is_cancelled(self) -> bool:
        """Return whether the child has started beating."""
        return self._beat.exists()


class InterruptWhenBeating:
    """Cancel token whose check raises KeyboardInterrupt once the child runs (a second Ctrl+C)."""

    def __init__(self, beat: Path) -> None:
        """Watch this heartbeat file."""
        self._beat = beat

    def is_cancelled(self) -> bool:
        """Return False until the child beats, then interrupt.

        Raises:
            KeyboardInterrupt: Once the heartbeat file exists.
        """
        if self._beat.exists():
            raise KeyboardInterrupt
        return False


def test_returns_exit_code_and_both_outputs() -> None:
    """A child writing more than a pipe buffer to both pipes is read to the end, not deadlocked."""
    completed = SubprocessRunner().run(_python(CHATTY), RUN_TIMEOUT_S, CancelFlag())

    assert (completed.returncode, len(completed.stdout), len(completed.stderr)) == (3, 1_000_000, 1_000_000)


def test_timeout_kills_the_child(tmp_path: Path) -> None:
    """A child outliving the timeout is killed and the runner raises."""
    beat = tmp_path / "beat"
    started = time.monotonic()

    with pytest.raises(ProcessTimeoutError):
        SubprocessRunner().run(_python(HEARTBEAT, str(beat)), 1.0, CancelFlag())

    assert time.monotonic() - started < 1.0 + KILL_BUDGET_S
    _assert_stopped(beat)


def test_cancel_kills_the_child(tmp_path: Path) -> None:
    """Setting the token kills a running child within the poll interval."""
    beat = tmp_path / "beat"

    with pytest.raises(CancelledError):
        SubprocessRunner().run(_python(HEARTBEAT, str(beat)), RUN_TIMEOUT_S, CancelWhenBeating(beat))

    _assert_stopped(beat)


def test_cancel_from_another_thread(tmp_path: Path) -> None:
    """The CLI sets the flag from its signal handler while the runner waits."""
    beat = tmp_path / "beat"
    flag = CancelFlag()
    canceller = threading.Thread(target=_cancel_once_beating, args=(beat, flag), daemon=True)
    canceller.start()

    with pytest.raises(CancelledError):
        SubprocessRunner().run(_python(HEARTBEAT, str(beat)), RUN_TIMEOUT_S, flag)

    canceller.join()
    _assert_stopped(beat)


def test_cancelled_before_start_runs_nothing(tmp_path: Path) -> None:
    """A token already set means the process is never started."""
    beat = tmp_path / "beat"
    flag = CancelFlag()
    flag.cancel()

    with pytest.raises(CancelledError):
        SubprocessRunner().run(_python(HEARTBEAT, str(beat)), RUN_TIMEOUT_S, flag)

    time.sleep(SILENCE_S)
    assert not beat.exists()


def test_interrupt_while_waiting_kills_the_child(tmp_path: Path) -> None:
    """Any exception during the wait (here a second Ctrl+C) kills the child before it propagates."""
    beat = tmp_path / "beat"

    with pytest.raises(KeyboardInterrupt):
        SubprocessRunner().run(_python(HEARTBEAT, str(beat)), RUN_TIMEOUT_S, InterruptWhenBeating(beat))

    _assert_stopped(beat)


def test_missing_executable_raises_os_error(tmp_path: Path) -> None:
    """A program that does not exist is an OSError for the caller to map."""
    with pytest.raises(OSError, match=r".") as caught:
        SubprocessRunner().run([str(tmp_path / "no-such-tool")], RUN_TIMEOUT_S, CancelFlag())

    assert isinstance(caught.value, FileNotFoundError)


def test_child_runs_in_its_own_session() -> None:
    """The child leads a new session, so a terminal Ctrl+C reaches only the app (plan m5 M5.4)."""
    if sys.platform == "win32":
        pytest.skip("sessions are POSIX; Windows uses CREATE_NEW_PROCESS_GROUP and a job object")
    completed = SubprocessRunner().run(_python("import os; print(os.getsid(0))"), RUN_TIMEOUT_S, CancelFlag())

    child_session = int(completed.stdout)
    assert child_session != os.getsid(0)


def _run_beating(beat: Path, cancel: CancelToken, outcome: list[BaseException | int]) -> None:
    try:
        # One poll as long as the run: the end is seen by `communicate`, not by a per-poll check.
        runner = SubprocessRunner(poll_interval_s=RUN_TIMEOUT_S)
        outcome.append(runner.run(_python(HEARTBEAT, str(beat)), RUN_TIMEOUT_S, cancel).returncode)
    except CancelledError as error:
        outcome.append(error)


def _quit_now_while_running(beat: Path, cancel: CancelFlag, *, flag_first: bool) -> list[BaseException | int]:
    outcome: list[BaseException | int] = []
    waiter = threading.Thread(target=_run_beating, args=(beat, cancel, outcome))
    waiter.start()
    _wait_for_beat(beat)
    assert running_tools.count() == 1
    if flag_first:
        cancel.cancel()
    kill_running_tools()
    waiter.join(KILL_BUDGET_S)
    assert not waiter.is_alive()
    _assert_stopped(beat)
    assert running_tools.count() == 0
    return outcome


@pytest.mark.skipif(sys.platform == "win32", reason="Windows: the kill-on-close job ends the tools with the process")
def test_quit_now_kills_running_tools(tmp_path: Path) -> None:
    """`kill_running_tools` (the GUI's "Quit now") kills a waited-for tool; the run is cancelled, not failed."""
    outcome = _quit_now_while_running(tmp_path / "heartbeat", CancelFlag(), flag_first=False)
    assert [type(ended) for ended in outcome] == [CancelledError]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows: the kill-on-close job ends the tools with the process")
def test_quit_now_with_the_flag_set_is_a_cancel(tmp_path: Path) -> None:
    """Killed while its cancel flag is set (close, then "Quit now"): `CancelledError`, never a result."""
    outcome = _quit_now_while_running(tmp_path / "heartbeat", CancelFlag(), flag_first=True)
    assert [type(ended) for ended in outcome] == [CancelledError]
