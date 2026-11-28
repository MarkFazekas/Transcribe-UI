"""The window for the New job page's tests: its calls finish before pytest-qt closes it (plan m6 M6.4)."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from PySide6.QtCore import QModelIndex, QPersistentModelIndex
from pytestqt.qtbot import QtBot

from support.gated import wait_for
from support.gui import untracked_window
from support.selection import HeldUploads
from transcribe_ui.gui.executor import RequestRunner
from transcribe_ui.gui.shell import MainWindow


def settled(qtbot: QtBot, window: MainWindow) -> None:
    """Wait until no call is in flight, so no pool thread still runs a request once the test's state closes.

    (Answers that would arrive after the window is gone are dropped by the window itself.)
    """
    wait_for(qtbot, lambda: not window.busy())


def settled_window(qtbot: QtBot, runner: RequestRunner, log_dir: Path) -> MainWindow:
    """Return the shown window over `runner`; pytest-qt lets its calls finish before it closes it."""
    window = untracked_window(runner, log_dir)
    qtbot.addWidget(window, before_close_func=partial(settled, qtbot))
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
