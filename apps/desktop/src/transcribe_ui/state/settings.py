"""The settings file: JSON in the user config dir, written atomically (temp file + `os.replace`).

Only `get_settings`/`update_settings`, the GUI (to fill request fields) and the composition
root (adapter configuration) read it; request handling never does (plan m5 §1). Several
processes may write it; the last writer wins.
"""

import logging
import os
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from transcribe_ui.api.classes import Settings
from transcribe_ui.state.constants import SETTINGS_REPLACE_ATTEMPTS, SETTINGS_REPLACE_DELAY_S, SETTINGS_TEMP_PREFIX
from transcribe_ui.state.errors import SettingsInvalidError, SettingsUnwritableError

logger = logging.getLogger(__name__)


@contextmanager
def _temp_file(folder: Path) -> Iterator[tuple[int, Path]]:
    """Yield a new temp file in the folder (descriptor, path); it is gone when the block ends."""
    folder.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=SETTINGS_TEMP_PREFIX, suffix=".json", dir=folder)
    temp_path = Path(temp_name)
    try:
        yield descriptor, temp_path
    finally:
        temp_path.unlink(missing_ok=True)


class SettingsStore:
    """Reads and writes the settings file; a missing file means the defaults."""

    def __init__(self, path: Path) -> None:
        """Use this settings file."""
        self._path = path

    def load(self) -> Settings:
        """Return the stored settings, or the defaults when there is no file yet.

        Raises:
            SettingsInvalidError: If the file cannot be read or is not valid settings.
        """
        try:
            raw = self._path.read_bytes()
        except FileNotFoundError:
            return Settings()
        except OSError as error:
            logger.exception("Cannot read the settings file %s", self._path)
            raise SettingsInvalidError from error
        try:
            return Settings.model_validate_json(raw)
        except ValidationError as error:
            logger.warning("The settings file %s is not valid: %s", self._path, error)
            raise SettingsInvalidError from error

    def save(self, settings: Settings) -> None:
        """Replace the file with these settings; readers see either the old or the new file.

        Raises:
            SettingsUnwritableError: If the file cannot be written.
        """
        document = f"{settings.model_dump_json(indent=2)}\n".encode()
        try:
            self._write(document)
        except OSError as error:
            logger.exception("Cannot write the settings file %s", self._path)
            raise SettingsUnwritableError from error

    def _write(self, document: bytes) -> None:
        with _temp_file(self._path.parent) as (descriptor, temp_path):
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(document)
                stream.flush()
                os.fsync(stream.fileno())
            self._replace(temp_path)

    def _replace(self, temp_path: Path) -> None:
        # Windows refuses to replace a file another process has open; such readers are brief.
        for attempt in range(1, SETTINGS_REPLACE_ATTEMPTS + 1):
            try:
                temp_path.replace(self._path)
            except PermissionError:
                if attempt == SETTINGS_REPLACE_ATTEMPTS:
                    raise
                time.sleep(SETTINGS_REPLACE_DELAY_S)
            else:
                return
