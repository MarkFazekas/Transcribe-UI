"""Closing the window with work in flight (Q30) and "Quit now".

With calls queued or running, or a run that is not final, the user confirms first ("Stop and
close?", with the cloud note while a run is not final; the close sends no request). Then every
pool is cleared (queued calls never start and complete as `cancelled`), running calls get their
cancel flag, and a modal "Finishing the current step" dialog waits for them. Its "Quit now"
kills running ffmpeg/ffprobe processes, flushes and closes the log, then ends the process with
`os._exit` (`sys.exit` would wait for the pool threads); every step is idempotent, so nothing
is lost.
"""

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtWidgets import QWidget

from transcribe_ui.gui.bridge import QtExecutor
from transcribe_ui.gui.constants import QUIT_NOW_EXIT_CODE
from transcribe_ui.gui.dialogs import CloseQuestion, FinishingDialog
from transcribe_ui.i18n.catalog import tr
from transcribe_ui.media.runner import kill_running_tools


def flush_log_handlers() -> None:
    """Flush and close the handlers of the root logger (the GUI log), so nothing is lost on `os._exit`."""
    for log_handler in list(logging.getLogger().handlers):
        log_handler.flush()
        log_handler.close()


@dataclass(frozen=True)
class QuitNow:
    """Ends the process at once: kill running tools, flush and close the log, then `os._exit`.

    Pool threads are not waited for; ffmpeg/ffprobe children are killed first, since on POSIX
    they would outlive the process.
    """

    flush_logs: Callable[[], None] = flush_log_handlers
    exit_process: Callable[[int], object] = os._exit
    kill_tools: Callable[[], None] = kill_running_tools

    def __call__(self) -> None:
        """Kill the running tools, flush the log, then end the process."""
        self.kill_tools()
        self.flush_logs()
        self.exit_process(QUIT_NOW_EXIT_CODE)


def _cloud_note() -> str:
    """Return the note added to the close question while a run is not final (Q30)."""
    return tr(
        "The cloud machine keeps working without the app: it stops itself when the work is done or at its "
        "time limit, and the app shows its results the next time it starts.",
    )


class CloseGuard:
    """Decides whether the window may close now, and runs the close question and the wait."""

    def __init__(
        self,
        window: QWidget,
        executor: QtExecutor,
        quit_now: Callable[[], None],
        run_active: Callable[[], bool],
    ) -> None:
        """Guard `window`; `run_active` tells whether a run is not final (the cloud note)."""
        self._window = window
        self._executor = executor
        self._quit_now = quit_now
        self._run_active = run_active
        self._allowed = False
        self._asking = False
        self.question: CloseQuestion | None = None
        self.finishing: FinishingDialog | None = None

    def allows_close(self) -> bool:
        """Return whether the window may close now; if not, ask (once) and close later."""
        run_active = self._run_active()
        if self._allowed or not (self._executor.pending() or run_active):
            return True
        if not self._asking:
            self._ask(cloud_note=_cloud_note() if run_active else None)
        return False

    def _ask(self, cloud_note: str | None) -> None:
        self._asking = True
        text = tr("Stop and close? The work continues from where it stopped the next time you start it.")
        question = CloseQuestion(text, cloud_note, self._window)
        question.accepted.connect(self._stop_and_close)
        question.rejected.connect(self._keep_open)
        self.question = question
        question.open()

    def _keep_open(self) -> None:
        self._asking = False

    def _stop_and_close(self) -> None:
        self._executor.shutdown()
        # Calls still queued were just cancelled; only running ones make the user wait.
        if self._executor.running():
            self.finishing = FinishingDialog(tr("Finishing the current step..."), self._quit_now, self._window)
            self.finishing.open()
        self._executor.when_idle(self._close_now)

    def _close_now(self) -> None:
        self._allowed = True
        if self.finishing is not None:
            self.finishing.accept()
        self._window.close()
