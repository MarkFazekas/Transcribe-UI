"""The GUI's smoke mode: a release build starts, answers its start-up and closes itself (plan m8 §1).

With `TRANSCRIBE_UI_SMOKE_TEST=1`, `run_gui` hands this module's hook the window. It logs every
answer the window receives, waits until the last start-up step proceeded and nothing is in flight,
then closes the window through its normal close handling (the set-up wizard, open on a machine
without R2 credentials, closes with it). The exit code is 0 only when every answer was `ok`,
start-up finished in time, nothing was logged at ERROR or above while the window was watched
(an exception escaping a Qt slot, which `gui/messages.py` logs; a critical or fatal message of
Qt, whose logger is a child of the app's) and, for Hungarian, Qt's own translation was
installed. Without the variable nothing changes.
"""

import logging
import os
from collections.abc import Callable, Mapping
from typing import override

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from transcribe_ui.api.classes import ErrorResponse, Request, Response
from transcribe_ui.constants import PACKAGE_NAME
from transcribe_ui.gui.constants import (
    SMOKE_TEST_DEADLINE_MS,
    SMOKE_TEST_FAILED_EXIT_CODE,
    SMOKE_TEST_ON,
    SMOKE_TEST_VARIABLE,
)
from transcribe_ui.gui.language import StartLanguages
from transcribe_ui.gui.shell import MainWindow
from transcribe_ui.gui.signals import StartupSlot
from transcribe_ui.i18n.constants import DEFAULT_LANGUAGE

logger = logging.getLogger(__name__)


def smoke_test_requested(environ: Mapping[str, str] = os.environ) -> bool:
    """Return whether the environment asks for the smoke mode."""
    return environ.get(SMOKE_TEST_VARIABLE) == SMOKE_TEST_ON


class _ErrorRecords(logging.Handler):
    """Fails the smoke test on every record at ERROR or above that the smoke test did not write."""

    def __init__(self, fail: Callable[[str], None]) -> None:
        super().__init__(logging.ERROR)
        self._fail = fail

    @override
    def emit(self, record: logging.LogRecord) -> None:
        if record.name != logger.name:
            source = f"{record.levelname} by {record.name}"
            self._fail(f"logged {source}: {record.getMessage()}")


class SmokeTest:
    """Watches one window's start-up, closes the window when it is done and gives the exit code."""

    def __init__(self, languages: StartLanguages) -> None:
        """Check the languages resolved at start-up; nothing is attached yet."""
        self.failures: list[str] = []
        self._window: MainWindow | None = None
        self._finished = False
        self._errors = _ErrorRecords(self._fail)
        logger.info(
            "Smoke test: language %s, Qt's own translation %s",
            languages.shown,
            "installed" if languages.qt_translated else "not installed",
        )
        if languages.shown != DEFAULT_LANGUAGE and not languages.qt_translated:
            self._fail(f"Qt's own {languages.shown} translation did not load")

    def attach(self, window: MainWindow) -> None:
        """Watch `window` (the `on_window` hook of `run_gui`, called before the event loop starts)."""
        self._window = window
        # The app's loggers (the messages of Qt included) propagate to the package's logger.
        logging.getLogger(PACKAGE_NAME).addHandler(self._errors)
        context = window.context
        context.executor.observe(self._note)
        # Registered last in the last slot, so it runs after every other start-up step proceeded.
        context.hooks.register(max(StartupSlot), self._steps_done)
        deadline = QTimer(window)
        deadline.setSingleShot(True)
        deadline.timeout.connect(self._overdue)
        deadline.start(SMOKE_TEST_DEADLINE_MS)

    def detach(self) -> None:
        """Stop watching the log (idempotent)."""
        logging.getLogger(PACKAGE_NAME).removeHandler(self._errors)

    def exit_code(self, loop_code: int) -> int:
        """Return the GUI's exit code: the event loop's when it failed, else the smoke test's verdict."""
        self.detach()
        if loop_code:
            return loop_code
        if not self._finished:
            self._fail("the window closed before the start-up finished")
        if self.failures:
            logger.error("Smoke test failed: %s", "; ".join(self.failures))
            return SMOKE_TEST_FAILED_EXIT_CODE
        logger.info("Smoke test passed")
        return 0

    def _fail(self, reason: str) -> None:
        logger.error("Smoke test: %s", reason)
        self.failures.append(reason)

    def _note(self, request: Request, response: Response) -> None:
        if isinstance(response, ErrorResponse):
            error = response.error
            answer = f"{error.code}: {error.message}"
            self._fail(f"{request.action} answered {answer}")
        else:
            logger.info("Smoke test: %s answered %s", request.action, response.status)

    def _steps_done(self, proceed: Callable[[], None]) -> None:
        proceed()
        logger.info("Smoke test: every start-up step proceeded")
        self._close_later()

    def _close_later(self) -> None:
        window = self._window
        if window is not None:
            # Close from the event loop, not from inside the callback that delivered the last answer;
            # with the window as context, the timer dies with it.
            QTimer.singleShot(0, window, self._close)

    def _close(self) -> None:
        window = self._window
        if window is None or self._finished:
            return
        if window.busy():
            # A request is still in flight (an answer may have started another): wait for it too.
            window.context.executor.when_idle(self._close_later)
            return
        self._finished = True
        logger.info("Smoke test: closing the window")
        if not window.close():
            self._fail("the window did not close")
            QApplication.exit(SMOKE_TEST_FAILED_EXIT_CODE)

    def _overdue(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._fail(f"the start-up did not finish within {SMOKE_TEST_DEADLINE_MS} ms")
        QApplication.exit(SMOKE_TEST_FAILED_EXIT_CODE)
