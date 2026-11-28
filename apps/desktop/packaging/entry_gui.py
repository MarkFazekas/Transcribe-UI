"""Entry script of the GUI executable `transcribe-ui-gui` (PyInstaller starts its analysis here).

An exception escaping the GUI would make the windowed Windows bootloader show a modal traceback
dialog that waits for a click (a smoke test would wait for its timeout). Instead it is written
to stderr where the process has one, logged to the GUI log, and the exit code is 1. The app is
imported inside the guard, so a failing import is caught too; when the app's logging cannot be
imported or written either, stderr is all there is.
"""

import logging
import sys
import traceback
from contextlib import suppress

# Exit code of a crash (the CLI's `EXIT_ERROR`; not imported, since importing the app may be what failed).
CRASH_EXIT_CODE = 1
# Logger of the crash record.
CRASH_LOGGER = "transcribe_ui.gui.entry"


def log_crash(error: Exception) -> None:
    """Log `error` to the GUI log (raises when the app's logging cannot be imported or used)."""
    # Imported here: a failure to import them must not hide the crash being reported.
    from transcribe_ui.gui.constants import GUI_LOG_FILE_NAME  # noqa: PLC0415, WPS433 -- a deliberate late import (comment above)
    from transcribe_ui.state.logs import log_to_file, user_log_file  # noqa: PLC0415, WPS433 -- a deliberate late import (comment above)

    with log_to_file(user_log_file(GUI_LOG_FILE_NAME)):
        logging.getLogger(CRASH_LOGGER).error("The GUI ended with an unexpected error", exc_info=error)


def report_crash(error: Exception) -> None:
    """Print `error` to stderr when there is one, then log it to the GUI log if that still works."""
    # Each is a last resort on its own: a closed or broken stderr must not keep the crash out of the
    # log, and a log that cannot be written must not hide what stderr already has.
    if sys.stderr is not None:
        with suppress(Exception):
            traceback.print_exception(error)
    with suppress(Exception):
        log_crash(error)


def main() -> int:
    """Run the GUI; return 1 for an exception escaping it (`SystemExit` passes through)."""
    try:
        # Imported inside the guard, so an import failure is reported like any other crash.
        from transcribe_ui.gui.main import run  # noqa: PLC0415, WPS433 -- a deliberate late import (comment above)

        run()
    except Exception as error:  # noqa: BLE001 -- the last resort of the process: reported, then exit 1
        report_crash(error)
        return CRASH_EXIT_CODE
    return 0


if __name__ == "__main__":
    sys.exit(main())
