"""The settings file: JSON in the user config dir, written atomically (temp file + `os.replace`).

It is read by `get_settings`/`update_settings`, by the GUI (to fill request fields), by
the media tool lookup (the ffmpeg/ffprobe override, `tool_paths`, read when a tool is run) and
by both frontends at start-up (`language`, through `Backend.language_setting`).
Other request handling never reads it: machine-dependent inputs are request fields (plan m5
§1). Several processes may write it; the last writer wins.

Compatibility: reading starts from the defaults and takes every field the app knows from the
file, so a file of an older app (fields missing) or a newer one (unknown fields) reads fine.
Saving writes only the fields this app knows: settings a newer app added are dropped when an
older app saves.
"""

import json
import logging
import os
from collections.abc import Mapping
from functools import partial
from pathlib import Path

from pydantic import JsonValue, TypeAdapter, ValidationError

from transcribe_contracts.constants import DEFAULT_LANGUAGES
from transcribe_ui.api.classes import LanguageSetting, PlacementDefaults, Settings
from transcribe_ui.discovery.constants import DEFAULT_AUDIO_EXTENSIONS, DEFAULT_VIDEO_EXTENSIONS
from transcribe_ui.state.classes import ToolPaths
from transcribe_ui.state.constants import (
    DEFAULT_LANGUAGE_SETTING,
    DEFAULT_PLACEMENT_FOLDER_COUNT,
    DEFAULT_PLACEMENT_STRATEGY,
    LANGUAGE_FIELD,
)
from transcribe_ui.state.errors import SettingsInvalidError, SettingsUnwritableError
from transcribe_ui.state.files import remove_stale_temp_files, sync_folder, temp_file, with_sharing_retries

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
        language=DEFAULT_LANGUAGE_SETTING,
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


class SettingsStore:
    """Reads and writes the settings file; a missing file means the defaults."""

    def __init__(self, path: Path) -> None:
        """Use this settings file."""
        self._path = path
        # The "not valid" warning is logged once per store (per process), not at every read.
        self._warned = False

    def load(self) -> Settings:
        """Return the stored settings, or the defaults when there is no file yet.

        Raises:
            SettingsInvalidError: If the file cannot be read or is not valid settings.
        """
        stored = self._read_object()
        if stored is None:
            return default_settings()
        defaults = default_settings().model_dump(mode="json")
        try:
            return Settings.model_validate(_known_fields(defaults, stored))
        except ValidationError as error:
            raise self._invalid(error) from error

    def tool_paths(self) -> ToolPaths:
        """Return only the ffmpeg/ffprobe override; other fields are not checked.

        Raises:
            SettingsInvalidError: If the file cannot be read or a path field is not valid.
        """
        stored = self._read_object()
        if stored is None:
            return ToolPaths()
        try:
            return ToolPaths.model_validate(stored)
        except ValidationError as error:
            raise self._invalid(error) from error

    def language(self) -> LanguageSetting:
        """Return the language setting; `system` when it cannot be read (other fields are not checked).

        The frontends read it once at start-up, before anything else: a broken settings file must
        not keep the app from starting, and a broken field elsewhere must not change the language.
        """
        try:
            stored = self._read_object()
        except SettingsInvalidError:
            return DEFAULT_LANGUAGE_SETTING
        if stored is None or LANGUAGE_FIELD not in stored:
            return DEFAULT_LANGUAGE_SETTING
        try:
            return TypeAdapter[LanguageSetting](LanguageSetting).validate_python(stored[LANGUAGE_FIELD])
        except ValidationError as error:
            self._invalid(error)
            return DEFAULT_LANGUAGE_SETTING

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

    def _read_object(self) -> JsonObject | None:
        """Return the file's JSON object, or None when there is no file.

        Raises:
            SettingsInvalidError: If the file cannot be read or holds no JSON object.
        """
        try:
            raw = with_sharing_retries(self._path.read_bytes)
        except FileNotFoundError:
            return None
        except OSError as error:
            logger.exception("Cannot read the settings file %s", self._path)
            raise SettingsInvalidError from error
        try:
            stored = json.loads(raw)
        except ValueError as error:
            raise self._invalid(error) from error
        if not isinstance(stored, dict):
            raise self._invalid(TypeError("the settings file does not hold a JSON object"))
        return stored

    def _invalid(self, error: Exception) -> SettingsInvalidError:
        if not self._warned:
            self._warned = True
            logger.warning("The settings file %s is not valid: %s", self._path, error)
        return SettingsInvalidError()

    def _write(self, document: bytes) -> None:
        folder = self._path.parent
        with temp_file(folder) as (descriptor, temp_path):
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(document)
                stream.flush()
                os.fsync(stream.fileno())
            with_sharing_retries(partial(temp_path.replace, self._path))
        try:
            sync_folder(folder)
        except OSError:
            # The new file is in place; only its durability after a power loss is uncertain.
            logger.warning("Cannot flush the settings folder %s", folder, exc_info=True)
        remove_stale_temp_files(folder)
