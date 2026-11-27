"""Composition root: builds the backend with its real adapters (tests inject fakes instead)."""

from functools import partial
from pathlib import Path

from transcribe_ui.api.backend import Backend
from transcribe_ui.api.ports import Ports, SystemClock, UlidSource
from transcribe_ui.media.adapter import FfmpegMediaTools
from transcribe_ui.media.tools import ToolLocator, ToolOverrides, bundle_dir
from transcribe_ui.state.paths import AppPaths
from transcribe_ui.state.settings import SettingsStore
from transcribe_ui.state.store import LocalState


def build_backend(paths: AppPaths | None = None) -> Backend:
    """Return the backend used by the frontends (the user's directories unless `paths` is given).

    Nothing is read here: the settings file is read when a tool is first needed.
    """
    clock = SystemClock()
    state = LocalState(AppPaths.for_user() if paths is None else paths)
    media = media_tools(state.settings)
    ids = UlidSource(clock)
    return Backend(Ports(clock=clock, ids=ids, media=media, state=state))


def media_tools(settings: SettingsStore) -> FfmpegMediaTools:
    """Return the real media tools, with the ffmpeg/ffprobe override read from the settings.

    The override is read at every tool lookup, so a changed setting applies to the next call
    and a broken settings file gives `settings_invalid` only to requests that run a tool.
    """
    locator = ToolLocator(load_overrides=partial(settings_overrides, settings), bundle_dir=bundle_dir())
    return FfmpegMediaTools(locator)


def settings_overrides(settings: SettingsStore) -> ToolOverrides:
    """Return the ffmpeg/ffprobe paths set in the settings.

    Raises:
        SettingsInvalidError: If the settings file cannot be read or is not valid.
    """
    current = settings.load()
    return ToolOverrides(ffmpeg=_optional_path(current.ffmpeg_path), ffprobe=_optional_path(current.ffprobe_path))


def _optional_path(text: str | None) -> Path | None:
    return None if text is None else Path(text)
