"""Entry script of the GUI executable `transcribe-ui-gui` (PyInstaller starts its analysis here).

An exception escaping the GUI's `run` would make the windowed Windows bootloader show a modal
traceback dialog that waits for a click (a smoke test would wait for its timeout). Instead it
is logged to the GUI log, written to stderr where the process has one, and the exit code is 1.
"""

import logging
import sys
import traceback

from transcribe_ui.cli.constants import EXIT_ERROR
from transcribe_ui.gui.constants import GUI_LOG_FILE_NAME
from transcribe_ui.gui.main import run
from transcribe_ui.state.logs import log_to_file, user_log_file

# Logger of the crash record.
logger = logging.getLogger("transcribe_ui.gui.entry")


def report_crash(error: Exception) -> None:
    """Log `error` to the GUI log, and print it to stderr when there is one."""
    if sys.stderr is not None:
        traceback.print_exception(error)
    with log_to_file(user_log_file(GUI_LOG_FILE_NAME)):
        logger.error("The GUI ended with an unexpected error", exc_info=error)


if __name__ == "__main__":
    try:
        run()
    except Exception as error:  # noqa: BLE001 -- the last resort of the process: logged by report_crash, then exit 1
        report_crash(error)
        sys.exit(EXIT_ERROR)
