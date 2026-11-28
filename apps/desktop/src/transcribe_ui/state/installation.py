"""The installation id (architecture §3.9 "Downloading twice", Q35): who uploaded a job.

A random id (a ULID, not hardware-based), kept as the file `installation_id` in the data dir.
It is created on first use and written atomically (a temporary file, fsync, then `os.replace`), so a
crash never leaves a half-written id; a file that does not hold a valid id is replaced by a new
one the same way; a file that cannot be read (permissions, a disk error) is an error, never
replaced (the only cost of a replacement: this installation's earlier uploads then read as another
computer's, which asks the stronger cleanup confirmation). Requests that use it have opened the
database, so this process holds the writer lock: no other process writes the file meanwhile, and
a lock in the object serialises the threads of this one.
"""

import os
import re
import threading
from collections.abc import Callable
from pathlib import Path

from transcribe_contracts.constants import ULID_PATTERN
from transcribe_ui.state.constants import INSTALLATION_ID_TEMP_SUFFIX


class InstallationIdFile:
    """The installation id of this data dir, created on first use."""

    def __init__(self, path: Path, new_id: Callable[[], str]) -> None:
        """Keep the id in `path`; `new_id` makes a new one (a fresh ULID in production)."""
        self._path = path
        self._new_id = new_id
        self._lock = threading.Lock()
        self._known: str | None = None

    def current(self) -> str:
        """Return the installation id, creating a missing file (or replacing one without a valid id) first.

        Raises:
            OSError: The file could not be read (other than missing) or written; a file that
                exists but cannot be read is never replaced, so the id is not lost.
        """
        with self._lock:
            if self._known is None:
                self._known = self._read() or self._write()
            return self._known

    def _read(self) -> str | None:
        try:
            text = self._path.read_text(encoding="ascii").strip()
        except (FileNotFoundError, UnicodeDecodeError):
            return None
        return text if re.fullmatch(ULID_PATTERN, text) else None

    def _write(self) -> str:
        found = self._new_id()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        part_file = self._path.with_name(self._path.name + INSTALLATION_ID_TEMP_SUFFIX)
        with part_file.open("w", encoding="ascii") as output:
            output.write(found)
            output.flush()
            os.fsync(output.fileno())
        part_file.replace(self._path)
        return found
