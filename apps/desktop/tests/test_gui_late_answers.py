"""Answers that arrive after the window is gone reach no view (plan m6 §1 "Threading")."""

from collections.abc import Iterator
from pathlib import Path

import pytest
import shiboken6
from pytestqt.qtbot import QtBot

from support.gated import WAIT_MS, GatedRunner, wait_for
from support.gui import untracked_window
from transcribe_ui.gui.constants import PAGE_SETTINGS
from transcribe_ui.gui.settings.view import SettingsPage
from transcribe_ui.gui.shell import MainWindow

# How long the test lets the event loop deliver queued answers, in milliseconds.
EVENTS_MS = 200
# The request held until the window is gone.
HELD = "get_settings"


@pytest.fixture
def gated() -> GatedRunner:
    """Return a runner that holds `get_settings` until released."""
    return GatedRunner((HELD,))


@pytest.fixture
def window(gated: GatedRunner, tmp_path: Path) -> Iterator[MainWindow]:
    """Return a shown window pytest-qt does not track; afterwards the gate opens and the window is deleted.

    So a failing test can neither leave the held call looping on its pool thread nor keep the window.
    """
    created = untracked_window(gated, tmp_path)
    created.show()
    yield created
    gated.release()
    if shiboken6.isValid(created):
        shiboken6.delete(created)


def test_answers_after_the_window_is_gone_are_dropped(qtbot: QtBot, gated: GatedRunner, window: MainWindow) -> None:
    """An answer arriving after the window was deleted reaches no view (it would touch deleted widgets)."""
    context = window.context
    page = window.pages.page(PAGE_SETTINGS)
    assert isinstance(page, SettingsPage)
    # Any view-model of the window would do: its view repaints when the answer arrives.
    page.models.preferences.load()
    wait_for(qtbot, lambda: gated.started(HELD) == 1)
    shiboken6.delete(window)
    gated.release()
    assert context.executor.wait_for_threads(WAIT_MS)
    # The queued answer is delivered (and dropped) here; a view reached would raise in the event loop.
    qtbot.wait(EVENTS_MS)
    assert context.executor.pending() == 0
