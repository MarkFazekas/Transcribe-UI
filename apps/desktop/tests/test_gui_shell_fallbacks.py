"""The shell's answer to action signals nobody handles, in a window without areas (plan m6 M6.2).

Independent of which areas are built: every area that handles an action signal would hide the
fallback in a full window.
"""

from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from support.gated import UPLOAD, GatedRunner
from support.gui import window_over
from transcribe_ui.gui.shell import MainWindow
from transcribe_ui.gui.signals import ACTION_SIGNALS

BATCH = "batch_20261127_120000"


@pytest.mark.parametrize("signal_name", ACTION_SIGNALS)
def test_unhandled_actions_say_not_available(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    signal_name: str,
) -> None:
    """In a window without areas, each action signal says "Not available yet"; a connected one reaches its handler."""
    monkeypatch.setattr("transcribe_ui.gui.shell.install_areas", _no_areas)
    window = window_over(qtbot, GatedRunner((UPLOAD,)), tmp_path)
    signal = getattr(window.context.signals, signal_name)
    arguments = (BATCH, "audio") if signal_name in {"retry_jobs", "clean_up"} else (BATCH,)
    signal.emit(*arguments)
    assert window.statusBar().currentMessage() == "Not available yet."
    window.statusBar().clearMessage()
    received: list[tuple[object, ...]] = []
    window.connect_handler(signal_name, lambda *given: received.append(given))
    signal.emit(*arguments)
    assert (received, window.statusBar().currentMessage()) == ([arguments], "")


def _no_areas(_window: MainWindow) -> None:
    """Install no area (the shell's own fallbacks only)."""
