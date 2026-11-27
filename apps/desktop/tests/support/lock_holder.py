"""Holds the writer lock of a test root from a second process (the GUI's `app_busy` notice test).

Usage: `python lock_holder.py <root>`, with `apps/desktop/tests` on `PYTHONPATH`. It opens the
database under `<root>` (taking the writer lock), prints `locked`, and gives the lock up when a
line arrives on stdin (or stdin closes).
"""

import sys
from contextlib import closing
from pathlib import Path

from support.fakes import app_paths_under
from transcribe_ui.state.store import LocalState


def run() -> None:
    """Hold the lock of the root given on the command line until stdin says otherwise."""
    state = LocalState(app_paths_under(Path(sys.argv[1])))
    with closing(state):
        state.open_database()
        sys.stdout.write("locked\n")
        sys.stdout.flush()
        sys.stdin.readline()


if __name__ == "__main__":
    run()
