"""Finding ffmpeg and ffprobe (architecture §3.4).

Order: the path set in the settings, then the build bundled with the app (PyInstaller, M8), then
`PATH` (development runs without a bundle). So a `PATH` ffmpeg of unknown version never replaces
the tested bundled one, and a wrong override is reported instead of silently skipped.
"""

import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from transcribe_ui.api.errors import DomainError
from transcribe_ui.media.classes import Tool
from transcribe_ui.media.constants import ERROR_MEDIA_TOOL_NOT_FOUND, FFMPEG, WINDOWS_EXECUTABLE_SUFFIX

# Looks a command up on `PATH` (`shutil.which` signature, narrowed).
type Which = Callable[[str], str | None]


@dataclass(frozen=True)
class ToolOverrides:
    """Paths of ffmpeg and ffprobe set by the user; None means "not set"."""

    ffmpeg: Path | None = None
    ffprobe: Path | None = None

    def for_tool(self, tool: Tool) -> Path | None:
        """Return the override of one tool."""
        return self.ffmpeg if tool == FFMPEG else self.ffprobe


def _which(command: str) -> str | None:
    return shutil.which(command)


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
