"""The Qt timer behind the Cloud area's `Timer` port (the poll's interval, the countdown's second)."""

from PySide6.QtCore import QObject, QTimer

from transcribe_ui.gui.cloud.ports import Tick


class QtTimer:
    """A repeating `QTimer` on the GUI thread that calls its tick at each interval."""

    def __init__(self, interval_ms: int, tick: Tick, parent: QObject) -> None:
        """Call `tick` every `interval_ms` once started; the timer lives as long as `parent`."""
        self._timer = QTimer(parent)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(tick)

    @property
    def active(self) -> bool:
        """Return whether the timer runs."""
        return self._timer.isActive()

    def start(self) -> None:
        """Start the timer (a running one starts its interval again)."""
        self._timer.start()

    def stop(self) -> None:
        """Stop the timer."""
        self._timer.stop()
