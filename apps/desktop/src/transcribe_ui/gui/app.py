"""GUI start-up: `run_gui` builds the backend, the window and the bus, then runs the event loop of Qt.

Imported by `gui/main.py` (the `transcribe-ui-gui` executable, A20) only after the display
check. Messages of Qt go to the GUI log through `qInstallMessageHandler`, never to stderr.
"""

from collections.abc import Callable
from contextlib import closing

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from transcribe_ui.api.backend import Backend
from transcribe_ui.api.wiring import build_backend
from transcribe_ui.constants import DISTRIBUTION_NAME
from transcribe_ui.gui.activity import Activity
from transcribe_ui.gui.bridge import QtExecutor
from transcribe_ui.gui.closing import QuitNow
from transcribe_ui.gui.context import GuiContext
from transcribe_ui.gui.messages import exceptions_to_log, qt_messages_to_log
from transcribe_ui.gui.shell import MainWindow, build_shell
from transcribe_ui.gui.signals import GuiSignals, StartupHooks
from transcribe_ui.gui.writer_lock import WriterLockGate

# Builds the backend of the GUI (tests: seeded ports).
type BackendFactory = Callable[[], Backend]
# Called with the window before the event loop starts (tests close it from a timer).
type WindowHook = Callable[[MainWindow], None]


def application() -> QApplication:
    """Return the running `QApplication`, creating it on the first call."""
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([DISTRIBUTION_NAME])


def build_context(backend: Backend, quit_now: QuitNow | None = None) -> GuiContext:
    """Return the executor, the bus and the shared view-models of a window over `backend`."""
    executor = QtExecutor(backend)
    return GuiContext(
        executor=executor,
        signals=GuiSignals(),
        hooks=StartupHooks(),
        activity=Activity(executor),
        lock=WriterLockGate(executor),
        log_dir=backend.log_dir,
        quit_now=QuitNow() if quit_now is None else quit_now,
    )


def _run_window(backend: Backend, quit_now: QuitNow | None, on_window: WindowHook | None) -> int:
    app = application()
    context = build_context(backend, quit_now)
    window = build_shell(context)
    window.show()
    QTimer.singleShot(0, context.hooks.run)
    if on_window is not None:
        on_window(window)
    code = app.exec()
    context.executor.wait_for_threads()
    return code


def run_gui(
    make_backend: BackendFactory = build_backend,
    *,
    quit_now: QuitNow | None = None,
    on_window: WindowHook | None = None,
) -> int:
    """Run the GUI until its window closes; return the exit code.

    The start-up hooks run once the window shows. On a normal close the local state of the
    backend is closed last.
    """
    with qt_messages_to_log(), exceptions_to_log(), closing(make_backend()) as backend:
        return _run_window(backend, quit_now, on_window)
