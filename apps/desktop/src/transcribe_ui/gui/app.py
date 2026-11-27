"""GUI start-up: `run_gui` builds the backend, the window and the bus, then runs the event loop of Qt.

Imported by `gui/main.py` (the `transcribe-ui-gui` executable, A20) only after the display
check. Messages of Qt go to the GUI log through `qInstallMessageHandler`, never to stderr.
"""

import logging
from collections.abc import Callable
from contextlib import ExitStack, closing

import shiboken6
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from transcribe_ui.api.backend import Backend
from transcribe_ui.api.wiring import build_backend
from transcribe_ui.constants import DISTRIBUTION_NAME
from transcribe_ui.gui.closing import QuitNow
from transcribe_ui.gui.constants import SHUTDOWN_WAIT_MS
from transcribe_ui.gui.context import build_context
from transcribe_ui.gui.language import use_language
from transcribe_ui.gui.messages import exceptions_to_log, qt_messages_to_log
from transcribe_ui.gui.shell import MainWindow, build_shell

logger = logging.getLogger(__name__)

# Builds the backend of the GUI (tests: seeded ports).
type BackendFactory = Callable[[], Backend]
# Returns the OS user interface language (`i18n.system.SystemLanguage`; tests replace the OS's).
type OsLanguage = Callable[[], str | None]
# Called with the window before the event loop starts (tests close it from a timer).
type WindowHook = Callable[[MainWindow], None]


def application() -> QApplication:
    """Return the running `QApplication`, creating it on the first call."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([DISTRIBUTION_NAME])


def _run_window(backend: Backend, quit_now: QuitNow | None, on_window: WindowHook | None) -> int:
    app = application()
    context = build_context(backend, quit_now)
    window = build_shell(context)
    window.show()
    QTimer.singleShot(0, context.hooks.run)
    if on_window is not None:
        on_window(window)
    code = app.exec()
    window.hide()
    # The loop can also end without the close dialog, for example when the session ends. Stop
    # what is still queued or running then, and wait for it only for a bounded time.
    context.executor.shutdown()
    # Answers still on their way must not reach the window deleted below.
    context.executor.close()
    if not context.executor.wait_for_threads(SHUTDOWN_WAIT_MS):
        logger.error("Requests still running %s ms after the window closed; ending the process", SHUTDOWN_WAIT_MS)
        context.quit_now()
    # No event loop runs any more, so `deleteLater` would never happen: delete the window now.
    shiboken6.delete(window)
    return code


def run_gui(
    make_backend: BackendFactory = build_backend,
    *,
    quit_now: QuitNow | None = None,
    on_window: WindowHook | None = None,
    os_language: OsLanguage | None = None,
) -> int:
    """Run the GUI until its window closes; return the exit code.

    The language of the texts is resolved first (the `language` setting, with `system` the OS
    language; `os_language` replaces the OS's in tests), so every text is shown in it. The
    start-up hooks run once the window shows. On a normal close the local state of the backend
    is closed last.
    """
    with qt_messages_to_log(), exceptions_to_log(), closing(make_backend()) as backend, ExitStack() as stack:
        use_language(backend.language_setting(), application(), stack, os_language)
        return _run_window(backend, quit_now, on_window)
