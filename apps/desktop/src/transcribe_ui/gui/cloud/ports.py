"""The ports of the Cloud area's view-models: the timer, the clock and the outlet to the window.

Qt-free. The poll's timer is injected (`QtTimer` in the window, a manual one in tests), so poll
timing is tested without sleeping; the clock only drives the remaining-time countdown, which is
display, never a request.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from transcribe_ui.gui.feedback import ConfirmationView, ErrorView, Fix

# Returns the current moment (UTC; the countdown measures to `estimated_finish_at`).
type Clock = Callable[[], datetime]
# Called at each interval of a timer.
type Tick = Callable[[], None]


class Timer(Protocol):
    """A repeating timer: once started, it calls its tick at each interval until stopped."""

    @property
    def active(self) -> bool:
        """Return whether the timer runs."""
        ...

    def start(self) -> None:
        """Start the timer (a running one starts its interval again)."""
        ...

    def stop(self) -> None:
        """Stop the timer."""
        ...


# Builds a timer from its interval in milliseconds and its tick.
type TimerFactory = Callable[[int, Tick], Timer]


class CloudOutlet(Protocol):
    """What the Cloud area tells the window: bus signals, confirmations, errors, fixes, the task-bar alert."""

    def emit(self, signal_name: str, *arguments: object) -> None:
        """Emit the bus signal `signal_name` (`gui/signals.py`) with `arguments`."""

    def confirm(self, view: ConfirmationView, on_confirm: Callable[[], None]) -> None:
        """Ask the confirmation; Confirm calls `on_confirm`."""

    def show_error(self, view: ErrorView, retry: Callable[[], object] | None) -> None:
        """Show an error of an action; `retry` re-sends it (the `app_busy` fix)."""

    def fix(self, fix: Fix) -> None:
        """Carry out an error's fix: open a page, or probe the writer lock again."""

    def alert(self) -> None:
        """Flash the window in the task bar (a run ended)."""
