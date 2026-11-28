"""The window icon is built from the bundled PNGs and set when the application is created (M8.3)."""

import os
import subprocess
import sys

import pytest
from PySide6.QtCore import QSize

from transcribe_ui.gui.constants import ICON_SIZES
from transcribe_ui.gui.icon import app_icon

# Prints the sizes of the window icon of an application created by `application()`.
PROBE = (
    "from transcribe_ui.gui.application import application\n"
    "for width in sorted(size.width() for size in application().windowIcon().availableSizes()):\n"
    "    print(width)\n"
)
# How long the probe process may run, in seconds.
PROBE_TIMEOUT_S = 60


@pytest.mark.usefixtures("qapp")
def test_app_icon_has_every_bundled_size() -> None:
    """The icon is not empty and offers the smallest and the largest size at their own pixels."""
    icon = app_icon()
    assert not icon.isNull()
    assert {size.width() for size in icon.availableSizes()} == set(ICON_SIZES)
    smallest, largest = min(ICON_SIZES), max(ICON_SIZES)
    assert icon.pixmap(QSize(smallest, smallest)).width() == smallest
    assert icon.pixmap(QSize(largest, largest)).width() == largest


def test_application_sets_the_window_icon() -> None:
    """A fresh process that creates the application through `application()` gets the icon.

    A subprocess, because the test session's own `QApplication` is created by pytest-qt first.
    """
    probe = subprocess.run(  # noqa: S603  # fixed arguments: this interpreter and a constant script
        [sys.executable, "-c", PROBE],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        check=False,
        timeout=PROBE_TIMEOUT_S,
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.split() == [str(size) for size in ICON_SIZES]
