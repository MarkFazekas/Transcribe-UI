"""Composition root: builds the backend with its real adapters (tests inject fakes instead)."""

from transcribe_ui.api.backend import Backend
from transcribe_ui.api.ports import Ports, SystemClock, UlidSource


def build_backend() -> Backend:
    """Return the backend used by the frontends."""
    clock = SystemClock()
    return Backend(Ports(clock=clock, ids=UlidSource(clock)))
