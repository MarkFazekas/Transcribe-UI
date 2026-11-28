"""Builds the GUI's main window over a test runner (a gated fake or a seeded backend)."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QMessageLogContext, QtMsgType, qInstallMessageHandler
from pytestqt.qtbot import QtBot

from transcribe_ui.gui.activity import Activity
from transcribe_ui.gui.bridge import QtExecutor
from transcribe_ui.gui.closing import QuitNow
from transcribe_ui.gui.context import GuiContext
from transcribe_ui.gui.executor import RequestRunner
from transcribe_ui.gui.shell import MainWindow, build_shell
from transcribe_ui.gui.signals import GuiSignals, StartupHooks
from transcribe_ui.gui.writer_lock import WriterLockGate


def _never() -> bool:
    return False


def window_over(
    qtbot: QtBot,
    runner: RequestRunner,
    log_dir: Path,
    quit_now: QuitNow | None = None,
    run_active: Callable[[], bool] = _never,
) -> MainWindow:
    """Return the shown main window over `runner` (closed by pytest-qt after the test)."""
    window = untracked_window(runner, log_dir, quit_now, run_active)
    qtbot.addWidget(window)
    window.show()
    return window


def untracked_window(
    runner: RequestRunner,
    log_dir: Path,
    quit_now: QuitNow | None = None,
    run_active: Callable[[], bool] = _never,
) -> MainWindow:
    """Return a main window over `runner` that pytest-qt does not close (a test that deletes it itself)."""
    executor = QtExecutor(runner)
    context = GuiContext(
        executor=executor,
        signals=GuiSignals(),
        hooks=StartupHooks(),
        activity=Activity(executor),
        lock=WriterLockGate(executor),
        log_dir=log_dir,
        quit_now=QuitNow() if quit_now is None else quit_now,
    )
    window = build_shell(context)
    # After the areas are installed: the cloud page sets its own while it installs.
    if run_active is not _never:
        context.run_active = run_active
    return window


@dataclass
class QtMessages:
    """Collects the messages Qt reports (the model tester reports its failures as warnings)."""

    messages: list[str] = field(default_factory=list)

    def __call__(self, kind: QtMsgType, _context: QMessageLogContext, message: str) -> None:
        """Keep every message above debug."""
        if kind != QtMsgType.QtDebugMsg:
            self.messages.append(message)


@contextmanager
def qt_messages() -> Iterator[QtMessages]:
    """Collect what Qt reports while the block runs (the handler before it is restored afterwards)."""
    collected = QtMessages()
    previous = qInstallMessageHandler(collected)
    try:
        yield collected
    finally:
        qInstallMessageHandler(previous)
