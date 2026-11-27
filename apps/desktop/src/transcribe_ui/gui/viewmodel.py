"""The base of the GUI's view-models (architecture §3.1 "GUI").

Qt-free: a view-model holds its widget state as data, builds requests, sends them through an
injected `Executor` and turns responses into display data. Views subscribe to its changes and
repaint from its state; they never build a request themselves.
"""

from collections.abc import Callable
from functools import partial

from transcribe_ui.gui.executor import Executor

# Told that a view-model's display data changed.
type ChangeListener = Callable[[], None]


class ViewModel:
    """State of one GUI area, the requests it sends and change notification for its views."""

    def __init__(self, executor: Executor) -> None:
        """Send requests through this executor."""
        self.executor = executor
        self._listeners: list[ChangeListener] = []

    def subscribe(self, listener: ChangeListener) -> Callable[[], None]:
        """Call `listener` after every change; return the function that unsubscribes it."""
        self._listeners.append(listener)
        return partial(self._unsubscribe, listener)

    def changed(self) -> None:
        """Tell the subscribed views that the display data changed."""
        for listener in list(self._listeners):
            listener()

    def _unsubscribe(self, listener: ChangeListener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)
