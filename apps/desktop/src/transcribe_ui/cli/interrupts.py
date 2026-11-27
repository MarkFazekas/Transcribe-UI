"""Ctrl+C handling of the CLI: the first interrupt cancels, a second one stops at once."""

import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import FrameType

from transcribe_ui.api.ports import CancelFlag

# What `signal.signal` returns: a Python handler, SIG_DFL/SIG_IGN, or None (set outside Python).
type SignalHandler = Callable[[int, FrameType | None], object] | int | signal.Handlers | None


class InterruptHandler:
    """SIGINT handler: the first Ctrl+C sets the cancel flag, a second one interrupts at once."""

    def __init__(self, flag: CancelFlag) -> None:
        """Set this flag on the first interrupt."""
        self._flag = flag

    def __call__(self, _signum: int, _frame: FrameType | None) -> None:
        """Handle one SIGINT.

        Raises:
            KeyboardInterrupt: On the second interrupt.
        """
        if self._flag.is_cancelled():
            raise KeyboardInterrupt
        self._flag.cancel()


@contextmanager
def interrupts_cancel(flag: CancelFlag) -> Iterator[None]:
    """Route Ctrl+C to the cancel flag while the block runs (main thread only)."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous: SignalHandler = signal.signal(signal.SIGINT, InterruptHandler(flag))
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, signal.SIG_DFL if previous is None else previous)
