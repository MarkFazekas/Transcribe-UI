"""What the main window and the areas share: the executor, the bus and the shared view-models."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from transcribe_ui.gui.activity import Activity
from transcribe_ui.gui.bridge import QtExecutor
from transcribe_ui.gui.closing import QuitNow
from transcribe_ui.gui.signals import GuiSignals, StartupHooks
from transcribe_ui.gui.writer_lock import WriterLockGate


def _never_active() -> bool:
    return False


@dataclass
class GuiContext:
    """What the window and the areas share."""

    executor: QtExecutor
    signals: GuiSignals
    hooks: StartupHooks
    activity: Activity
    lock: WriterLockGate
    # Folder of the GUI log ("Open log folder").
    log_dir: Path
    quit_now: QuitNow = field(default_factory=QuitNow)
    # Whether a run is not final (the cloud page sets it); the close question then adds the cloud note.
    run_active: Callable[[], bool] = _never_active
