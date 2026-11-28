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
from transcribe_ui.gui.closing import QuitNow
from transcribe_ui.gui.constants import QT_APPLICATION_ARGUMENTS, SHUTDOWN_WAIT_MS
from transcribe_ui.gui.context import build_context
from transcribe_ui.gui.language import StartLanguages, use_language
from transcribe_ui.gui.messages import exceptions_to_log, qt_messages_to_log
from transcribe_ui.gui.shell import MainWindow, build_shell
from transcribe_ui.gui.smoke import SmokeTest, smoke_test_requested

logger = logging.getLogger(__name__)

# Builds the backend of the GUI (tests: seeded ports).
type BackendFactory = Callable[[], Backend]
# Returns the OS user interface language (`i18n.system.SystemLanguage`; tests replace the OS's).
type OsLanguage = Callable[[], str | None]
# Starts the GUI again after its window closed (tests record the call instead).
type Relaunch = Callable[[], None]
# What runs once the window closed and the backend is closed: the restart, or nothing.
type AfterClose = Relaunch | None
# The exit code of the event loop, and what runs after the close.
type WindowRun = tuple[int, AfterClose]
# Called with the window before the event loop starts (tests close it from a timer).
type WindowHook = Callable[[MainWindow], None]


def application() -> QApplication:
    """Return the running `QApplication`, creating it on the first call."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication(list(QT_APPLICATION_ARGUMENTS))


def _run_window(
    backend: Backend,
    quit_now: QuitNow | None,
    on_window: WindowHook | None,
    languages: StartLanguages,
) -> WindowRun:
    app = application()
    context = build_context(backend, quit_now, languages.system)
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
    return code, context.restart.relaunch if context.restart.requested else None


def run_gui(
    make_backend: BackendFactory = build_backend,
    *,
    quit_now: QuitNow | None = None,
    on_window: WindowHook | None = None,
    os_language: OsLanguage | None = None,
    relaunch: Relaunch | None = None,
) -> int:
    """Run the GUI until its window closes; return the exit code.

    The language of the texts is resolved first (the `language` setting, with `system` the OS
    language; `os_language` replaces the OS's in tests), so every text is shown in it. The
    start-up hooks run once the window shows. On a normal close the local state of the backend
    is closed last. When the user chose to restart (a language change, Q32), the GUI is started
    again after that, so the new process can take the writer lock (`relaunch` replaces the start
    in tests). With `TRANSCRIBE_UI_SMOKE_TEST=1` and no `on_window` of its own, the smoke mode
    watches the start-up and closes the window (`gui/smoke.py`); its verdict is the exit code.
    """
    smoke: SmokeTest | None = None
    with qt_messages_to_log(), exceptions_to_log(), closing(make_backend()) as backend, ExitStack() as stack:
        languages = use_language(backend.language_setting(), application(), stack, os_language)
        if on_window is None and smoke_test_requested():
            smoke = SmokeTest(languages)
            on_window = smoke.attach
        code, after_close = _run_window(backend, quit_now, on_window, languages)
    if smoke is not None:
        code = smoke.exit_code(code)
    if after_close is not None:
        logger.info("Starting the GUI again to change the language")
        (after_close if relaunch is None else relaunch)()
    return code
