"""Watching a conversion's partial output from a thread (kill and cleanup tests)."""

import threading
import time
from pathlib import Path

from transcribe_ui.api.ports import CancelFlag
from transcribe_ui.media.constants import PARTIAL_SUFFIX

# Longest to wait for the tool to start writing its partial output.
WRITE_WAIT_S = 20.0
# How often the watcher looks.
WATCH_INTERVAL_S = 0.01


class PartialWatcher:
    """Waits for a partial output in `folder` to hold data, notes it, then sets the flag if given one.

    The partial file exists (empty) before the tool starts, so only data shows the tool writing.
    """

    def __init__(self, folder: Path, flag: CancelFlag | None = None) -> None:
        """Start watching the folder."""
        self.written = threading.Event()
        self._folder = folder
        self._flag = flag
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def join(self) -> None:
        """Wait for the watch to end."""
        self._thread.join()

    def _watch(self) -> None:
        deadline = time.monotonic() + WRITE_WAIT_S
        while not self._has_data() and time.monotonic() < deadline:
            time.sleep(WATCH_INTERVAL_S)
        if self._has_data():
            self.written.set()
        if self._flag is not None:
            self._flag.cancel()

    def _has_data(self) -> bool:
        try:
            return any(path.stat().st_size > 0 for path in self._folder.glob(f"*{PARTIAL_SUFFIX}"))
        except OSError:
            # The file was removed between listing and stat.
            return False
