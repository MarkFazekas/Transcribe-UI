"""The window for the New job page's tests: drained before pytest-qt closes it (plan m6 M6.4)."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from PySide6.QtCore import QModelIndex, QPersistentModelIndex
from pytestqt.qtbot import QtBot

from support.gated import wait_for
from support.selection import HeldUploads
from transcribe_ui.gui.activity import Activity
from transcribe_ui.gui.bridge import QtExecutor
from transcribe_ui.gui.context import GuiContext
from transcribe_ui.gui.executor import RequestRunner
from transcribe_ui.gui.shell import MainWindow, build_shell
from transcribe_ui.gui.signals import GuiSignals, StartupHooks
from transcribe_ui.gui.writer_lock import WriterLockGate


def drained(qtbot: QtBot, window: MainWindow) -> None:
    """Wait until no call is in flight, then drop answers still on their way (the widgets go next)."""
    wait_for(qtbot, lambda: not window.busy())
    window.context.executor.close()


def drained_window(qtbot: QtBot, runner: RequestRunner, log_dir: Path) -> MainWindow:
    """Return the shown window over `runner`; pytest-qt drains it before it closes it."""
    executor = QtExecutor(runner)
    context = GuiContext(
        executor=executor,
        signals=GuiSignals(),
        hooks=StartupHooks(),
        activity=Activity(executor),
        lock=WriterLockGate(executor),
        log_dir=log_dir,
    )
    window = build_shell(context)
    qtbot.addWidget(window, before_close_func=partial(drained, qtbot))
    window.show()
    return window


@contextmanager
def released(runner: HeldUploads) -> Iterator[HeldUploads]:
    """Release the held uploads when the block ends, however it ends."""
    try:
        yield runner
    finally:
        runner.release()


# A model index as a signal passes it.
type SignalIndex = QModelIndex | QPersistentModelIndex


@dataclass
class ChangedSpans:
    """Records each `dataChanged` as (rows, first column, last column)."""

    spans: list[tuple[int, int, int]] = field(default_factory=list)

    def __call__(self, first: SignalIndex, last: SignalIndex, _roles: object = None) -> None:
        """Record one signal (its roles are not needed)."""
        self.spans.append((last.row() - first.row() + 1, first.column(), last.column()))
