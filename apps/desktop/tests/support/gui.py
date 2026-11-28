"""Builds the GUI's main window over a test runner (a gated fake or a seeded backend)."""

from collections.abc import Callable
from pathlib import Path

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
        run_active=run_active,
    )
    return build_shell(context)
