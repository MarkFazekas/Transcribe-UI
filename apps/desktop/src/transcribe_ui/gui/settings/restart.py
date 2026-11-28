"""Restarting the GUI after a change of the language of the texts (Q32; Qt-free).

"Restart now" asks the window to close through the close handling of Q30; when the user keeps
it open there, the restart is withdrawn. Once the event loop has ended and the backend's local
state is closed (so the new process can take the writer lock), `run_gui` starts the GUI again.
"""

import logging
import os
import subprocess
import sys
from dataclasses import dataclass

from transcribe_ui.api.classes import LanguageSetting
from transcribe_ui.gui.settings.constants import PYINSTALLER_RESET_VARIABLE, RELAUNCH_SOURCE
from transcribe_ui.i18n import system
from transcribe_ui.i18n.catalog import active_language
from transcribe_ui.i18n.language import resolve_language

logger = logging.getLogger(__name__)


def changes_language(setting: LanguageSetting) -> bool:
    """Return whether `setting` shows the texts in another language than the running GUI (a restart applies it)."""
    return resolve_language(setting, system.os_language) != active_language()


def relaunch_command() -> list[str]:
    """Return the command that starts this GUI again: the one-file build itself, or the interpreter."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-c", RELAUNCH_SOURCE]


def relaunch_gui() -> None:
    """Start the GUI again as a separate process; a failure is logged (the user starts it by hand)."""
    environment = dict(os.environ)
    if getattr(sys, "frozen", False):
        # The new instance must unpack its own files: this one's are removed when it exits.
        environment[PYINSTALLER_RESET_VARIABLE] = "1"
    try:
        subprocess.Popen(relaunch_command(), env=environment, start_new_session=True)  # noqa: S603 -- fixed command.
    except OSError:
        logger.exception("The GUI could not be started again")


@dataclass
class RestartRequest:
    """Whether the GUI starts again after its window closed (`run_gui` starts it)."""

    requested: bool = False

    def request(self) -> None:
        """Start the GUI again once its window has closed."""
        self.requested = True

    def withdraw(self) -> None:
        """Do not start again (the user kept the window open)."""
        self.requested = False

    def relaunch(self) -> None:
        """Start the GUI again (called by `run_gui` once the window and the backend are closed)."""
        relaunch_gui()
