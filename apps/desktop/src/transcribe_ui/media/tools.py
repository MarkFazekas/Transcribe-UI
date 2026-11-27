"""Finding ffmpeg and ffprobe (architecture §3.4).

Order: the path set in the settings, then the build bundled with the app (PyInstaller, M8), then
`PATH` (development runs without a bundle). So a `PATH` ffmpeg of unknown version never replaces
the tested bundled one, and a wrong override is reported instead of silently skipped.
"""

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from transcribe_ui.api.errors import DomainError
from transcribe_ui.media.classes import Tool
from transcribe_ui.media.constants import DEFAULT_PATHEXT, ERROR_MEDIA_TOOL_NOT_FOUND, FFMPEG, WINDOWS_EXECUTABLE_SUFFIX

# Looks a command up on `PATH` (`search_path` with this process's environment).
type Which = Callable[[str], str | None]


@dataclass(frozen=True)
class ToolOverrides:
    """Paths of ffmpeg and ffprobe set by the user; None means "not set"."""

    ffmpeg: Path | None = None
    ffprobe: Path | None = None

    def for_tool(self, tool: Tool) -> Path | None:
        """Return the override of one tool."""
        return self.ffmpeg if tool == FFMPEG else self.ffprobe


def search_path(command: str, environ: Mapping[str, str], *, windows: bool) -> str | None:
    """Return the first executable named `command` in the absolute folders of `PATH`.

    Unlike `shutil.which`, the current folder is never searched (Windows searches it first), and
    neither are relative `PATH` entries, so a file dropped next to the user's media is never run.
    On Windows each `PATHEXT` extension is tried.
    """
    entries = environ.get("PATH", "").split(os.pathsep)
    folders = [Path(entry) for entry in entries if entry]
    names = [command]
    if windows:
        extensions = environ.get("PATHEXT", DEFAULT_PATHEXT).split(";")
        names = [f"{command}{extension.lower()}" for extension in extensions if extension]
    for folder in folders:
        if not folder.is_absolute():
            continue
        found = _executable_in(folder, names, windows=windows)
        if found is not None:
            return str(found)
    return None


def _executable_in(folder: Path, names: list[str], *, windows: bool) -> Path | None:
    for name in names:
        candidate = folder / name
        if candidate.is_file() and (windows or os.access(candidate, os.X_OK)):
            return candidate
    return None


def _which(command: str) -> str | None:
    return search_path(command, os.environ, windows=sys.platform == "win32")


@dataclass(frozen=True)
class ToolLocator:
    """Resolves a tool to an executable path, at the time it is needed."""

    overrides: ToolOverrides = field(default_factory=ToolOverrides)
    # Folder of the bundled builds; None when the app runs without a bundle.
    bundle_dir: Path | None = None
    which: Which = _which

    def resolve(self, tool: Tool) -> Path:
        """Return the executable to run for the tool.

        Raises:
            DomainError: `media_tool_not_found` if the override does not exist or nothing was found.
        """
        override = self.overrides.for_tool(tool)
        if override is not None:
            if override.is_file():
                return override
            message = f"The {tool} path set in the settings is not a file: {override}"
            raise DomainError(ERROR_MEDIA_TOOL_NOT_FOUND, message, {"tool": tool, "source": "settings"})
        if self.bundle_dir is not None:
            bundled = self.bundle_dir / executable_name(tool)
            if bundled.is_file():
                return bundled
        found = self.which(tool)
        if found is not None:
            return Path(found)
        message = f"{tool} was not found: set its path in the settings or put it on PATH."
        raise DomainError(ERROR_MEDIA_TOOL_NOT_FOUND, message, {"tool": tool, "source": "search"})


def executable_name(tool: Tool) -> str:
    """Return the file name of the tool's executable on this OS."""
    if sys.platform == "win32":
        return f"{tool}{WINDOWS_EXECUTABLE_SUFFIX}"
    return tool


def bundle_dir() -> Path | None:
    """Return the folder a PyInstaller build unpacks to (holding the bundled tools), or None."""
    if not getattr(sys, "frozen", False):
        return None
    unpacked = getattr(sys, "_MEIPASS", None)
    return Path(unpacked) if isinstance(unpacked, str) else None
