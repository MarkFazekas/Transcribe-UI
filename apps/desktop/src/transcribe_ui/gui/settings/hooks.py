"""What the settings area's view-models tell the window (Qt-free; the window passes its handlers in)."""

from collections.abc import Callable
from dataclasses import dataclass

from transcribe_ui.gui.feedback import ErrorView


def _nothing() -> None:
    """Default of a hook nobody listens to."""


def _drop(_view: ErrorView) -> None:
    """Default error hook: nobody shows errors (view-model tests read the view-model instead)."""


@dataclass(frozen=True)
class AreaHooks:
    """Handlers the window gives the settings area's view-models."""

    # Shows an error response the user has to see (the generic error dialog).
    on_error: Callable[[ErrorView], object] = _drop
    # The settings were saved (the window emits `settings_changed`).
    on_saved: Callable[[], object] = _nothing
    # A save changed the language the texts would be shown in (the window asks to restart).
    on_language_changed: Callable[[], object] = _nothing
