"""The settings file: JSON in the user config dir, written atomically (temp file + `os.replace`).

Only `get_settings`/`update_settings`, the GUI (to fill request fields) and the composition
root (adapter configuration) read it; request handling never does (plan m5 §1). Several
processes may write it; the last writer wins.

Compatibility: reading starts from the defaults and takes every field the app knows from the
file, so a file of an older app (fields missing) or a newer one (unknown fields) reads fine.
Saving writes only the fields this app knows: settings a newer app added are dropped when an
older app saves.
"""

import json
import logging
import os
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path

from pydantic import JsonValue, ValidationError

from transcribe_contracts.constants import DEFAULT_LANGUAGES
from transcribe_ui.api.classes import PlacementDefaults, Settings
from transcribe_ui.discovery.constants import DEFAULT_AUDIO_EXTENSIONS, DEFAULT_VIDEO_EXTENSIONS
from transcribe_ui.state.constants import (
    DEFAULT_PLACEMENT_FOLDER_COUNT,
    DEFAULT_PLACEMENT_STRATEGY,
    SETTINGS_REPLACE_DELAYS_S,
    SETTINGS_STALE_TEMP_S,
    SETTINGS_TEMP_PREFIX,
    SETTINGS_TEMP_SUFFIX,
)
from transcribe_ui.state.errors import SettingsInvalidError, SettingsUnwritableError

logger = logging.getLogger(__name__)

# A JSON object as read from the settings file.
type JsonObject = dict[str, JsonValue]


def default_settings() -> Settings:
    """Return the settings of a fresh installation."""
    return Settings(
        enabled_languages=DEFAULT_LANGUAGES,
        video_extensions=DEFAULT_VIDEO_EXTENSIONS,
        audio_extensions=DEFAULT_AUDIO_EXTENSIONS,
        include_hidden=False,
        ffmpeg_path=None,
        ffprobe_path=None,
        placement=PlacementDefaults(
            strategy=DEFAULT_PLACEMENT_STRATEGY,
            output_folder=None,
            folder_count=DEFAULT_PLACEMENT_FOLDER_COUNT,
            overwrite=False,
        ),
    )


def _known_fields(defaults: Mapping[str, JsonValue], stored: Mapping[str, JsonValue]) -> JsonObject:
    """Return the defaults overlaid with the stored values of the fields the defaults have."""
    merged = dict(defaults)
    for key, default in defaults.items():
        if key not in stored:
            continue
        stored_value = stored[key]
        if isinstance(default, dict) and isinstance(stored_value, dict):
            merged[key] = _known_fields(default, stored_value)
        else:
            merged[key] = stored_value
    return merged


@contextmanager
def _temp_file(folder: Path) -> Iterator[tuple[int, Path]]:
    """Yield a new temp file in the folder (descriptor, path); it is gone when the block ends."""
    folder.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=SETTINGS_TEMP_PREFIX, suffix=SETTINGS_TEMP_SUFFIX, dir=folder)
    temp_path = Path(temp_name)
    try:
        yield descriptor, temp_path
    finally:
        temp_path.unlink(missing_ok=True)


def _remove_stale_temp_files(folder: Path) -> None:
    """Remove temp files a crashed write left behind (old enough not to be another writer's)."""
    cutoff = time.time() - SETTINGS_STALE_TEMP_S
    for leftover in folder.glob(f"{SETTINGS_TEMP_PREFIX}*{SETTINGS_TEMP_SUFFIX}"):
        with suppress(OSError):
            if leftover.stat().st_mtime < cutoff:
                leftover.unlink()


def _sync_folder(folder: Path) -> None:
    """Make the rename durable (POSIX; Windows cannot open a folder for fsync)."""
    if sys.platform == "win32":
        return
    with ExitStack() as cleanup:
        descriptor = os.open(folder, os.O_RDONLY)
        cleanup.callback(os.close, descriptor)
        os.fsync(descriptor)


def _try_replace(temp_path: Path, target: Path) -> bool:
    """Replace the target; return False on a Windows sharing violation (worth retrying)."""
    try:
        temp_path.replace(target)
    except PermissionError:
        # Windows refuses while another process has the file open; elsewhere it is a real refusal.
        if sys.platform != "win32":
            raise
        return False
    return True


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
            return default_settings()
        except OSError as error:
            logger.exception("Cannot read the settings file %s", self._path)
            raise SettingsInvalidError from error
        try:
            return self._parse(raw)
        except (ValueError, ValidationError) as error:
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

    def _parse(self, raw: bytes) -> Settings:
        stored = json.loads(raw)
        if not isinstance(stored, dict):
            msg = "the settings file does not hold a JSON object"
            raise TypeError(msg)
        defaults = default_settings().model_dump(mode="json")
        return Settings.model_validate(_known_fields(defaults, stored))

    def _write(self, document: bytes) -> None:
        folder = self._path.parent
        with _temp_file(folder) as (descriptor, temp_path):
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(document)
                stream.flush()
                os.fsync(stream.fileno())
            self._replace(temp_path)
        _sync_folder(folder)
        _remove_stale_temp_files(folder)

    def _replace(self, temp_path: Path) -> None:
        # Backs off for about 2 s in all while Windows reports a sharing violation.
        for delay_s in SETTINGS_REPLACE_DELAYS_S:
            if _try_replace(temp_path, self._path):
                return
            time.sleep(delay_s)
        temp_path.replace(self._path)
