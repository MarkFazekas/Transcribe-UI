"""Copies of the results on the Global Volume, in the same layout as in R2.

The Global Volume has object-store semantics (no atomic rename), so files are written once as
a whole. The job result JSON is written last and marks a complete backup, as in R2. Backup
problems never stop the run: R2 is the primary copy.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class Backup:
    """Result files under a root folder, keyed by their R2 key."""

    def __init__(self, root: Path) -> None:
        """Keep the backups under `root` (created on first write)."""
        self._root = root

    def save(self, key: str, body: bytes, *, marker: bool) -> None:
        """Write a result file; a marker (the job JSON) is never overwritten.

        A non-marker file (the subtitle) of an unfinished earlier attempt is replaced, since
        only the marker makes a backup complete.
        """
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb" if marker else "wb") as output:
                output.write(body)
        except FileExistsError:
            logger.warning("backup %s exists already; kept", key)
        except OSError:
            logger.exception("could not back up %s", key)

    def load(self, key: str) -> bytes | None:
        """Return a backed-up file, or None if it is missing or unreadable."""
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            logger.exception("could not read backup %s", key)
            return None

    def _path(self, key: str) -> Path:
        return self._root.joinpath(*key.split("/"))
