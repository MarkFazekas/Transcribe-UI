"""Target-OS name rules of the placement planner (architecture §3.5).

The rules follow the injected path-flavour rule (`discovery/rules.py`), so the Windows rules are
tested on any OS: generated names are made valid (invalid characters to `_`, reserved device
names and trailing dots or spaces avoided), imported names are checked and refused.
"""

import ntpath
import posixpath
from dataclasses import dataclass
from pathlib import PurePath, PurePosixPath, PureWindowsPath

from transcribe_ui.discovery.rules import PathRules
from transcribe_ui.placement.constants import (
    MAX_NAME_LENGTH,
    POSIX_FORBIDDEN_CHARACTERS,
    REPLACEMENT_CHARACTER,
    SUBTITLE_EXTENSION,
    TEMP_TAIL_LENGTH,
    UTF16_UNIT_BYTES,
    WINDOWS_FORBIDDEN_CHARACTERS,
    WINDOWS_LAST_CONTROL_CHARACTER,
    WINDOWS_MAX_FOLDER_LENGTH,
    WINDOWS_MAX_PATH_LENGTH,
    WINDOWS_RESERVED_NAMES,
    WINDOWS_TRAILING_CHARACTERS,
)

# Rule keys of a path, part by part: equal keys are the same path.
type PathKey = tuple[str, ...]


def split_name(file_name: str) -> tuple[str, str]:
    """Return a file name's stem and its extension without the dot (empty when it has none)."""
    stem, extension = posixpath.splitext(file_name)
    return stem, extension.removeprefix(".")


def subtitle_name(stem: str, language: str) -> str:
    """Return the subtitle name `<stem>.<lang>.srt`."""
    return f"{stem}.{language}.{SUBTITLE_EXTENSION}"


def has_subtitle_extension(file_name: str) -> bool:
    """Return whether a file name ends with `.srt` (in any case)."""
    return file_name.lower().endswith(f".{SUBTITLE_EXTENSION}")


def is_clean_absolute(path: PurePath) -> bool:
    """Return whether a path is absolute and has no `..` part (so it cannot leave a folder)."""
    return path.is_absolute() and ".." not in path.parts


def _reserved(name: str) -> bool:
    # Windows reserves a device name also with an extension and with spaces before the dot.
    base = name.split(".", 1)[0].rstrip(" ")
    return base.upper() in WINDOWS_RESERVED_NAMES


def _with_trailing_replaced(name: str) -> str:
    kept = name.rstrip(WINDOWS_TRAILING_CHARACTERS)
    return kept + REPLACEMENT_CHARACTER * (len(name) - len(kept))


def _with_base_suffix(name: str) -> str:
    base, dot, rest = name.partition(".")
    return f"{base}{REPLACEMENT_CHARACTER}{dot}{rest}"


def _windows_problem(name: str) -> str | None:
    if name.endswith(tuple(WINDOWS_TRAILING_CHARACTERS)):
        return "ends with a dot or a space"
    if _reserved(name):
        return "is a name Windows reserves for a device"
    return None


@dataclass(frozen=True)
class TargetNames:
    """Paths and names under the target OS's rules (the injected path-flavour rule)."""

    rules: PathRules

    @property
    def windows(self) -> bool:
        """Return whether the target is Windows."""
        return self.rules.flavor == "windows"

    def pure(self, path: str) -> PurePath:
        """Return the path as a pure path of the target's flavour."""
        return PureWindowsPath(path) if self.windows else PurePosixPath(path)

    def normalised(self, path: str) -> str:
        """Return the path with `.` and `..` resolved lexically."""
        return ntpath.normpath(path) if self.windows else posixpath.normpath(path)

    def key(self, path: PurePath) -> PathKey:
        """Return the form under which the target considers two paths equal."""
        return tuple(map(self.rules.key, path.parts))

    def path_key(self, path: str) -> PathKey:
        """Return the key of a path given as text."""
        return self.key(self.pure(path))

    def length(self, name: str) -> int:
        """Return a name's length as the file system counts it: UTF-16 units on Windows, else UTF-8 bytes."""
        if self.windows:
            return len(name.encode("utf-16-le", "surrogatepass")) // UTF16_UNIT_BYTES
        return len(name.encode("utf-8", "surrogateescape"))

    def path_too_long(self, path: str) -> bool:
        """Return whether Windows cannot write the file: its temp file's path over MAX_PATH, or its folder too long to create."""
        if not self.windows:
            return False
        folder = str(self.pure(path).parent)
        too_long = self.length(path) + TEMP_TAIL_LENGTH > WINDOWS_MAX_PATH_LENGTH
        return too_long or self.length(folder) > WINDOWS_MAX_FOLDER_LENGTH

    def forbidden(self, character: str) -> bool:
        """Return whether the target does not allow the character in a name."""
        if not self.windows:
            return character in POSIX_FORBIDDEN_CHARACTERS
        control = ord(character) <= WINDOWS_LAST_CONTROL_CHARACTER
        return control or character in WINDOWS_FORBIDDEN_CHARACTERS

    def sanitised(self, name: str) -> str:
        """Return a generated file or folder name made valid for the target.

        Invalid characters become `_`; on Windows, trailing dots and spaces become `_` and a
        reserved device name gets `_` after its base (`CON.hu.srt` → `CON_.hu.srt`).
        """
        replaced = (REPLACEMENT_CHARACTER if self.forbidden(character) else character for character in name)
        cleaned = "".join(replaced)
        if not self.windows:
            return cleaned
        cleaned = _with_trailing_replaced(cleaned)
        return _with_base_suffix(cleaned) if _reserved(cleaned) else cleaned

    def problem(self, name: str) -> str | None:
        """Return why a user-given file or folder name is not valid on the target, or None."""
        if name in {"", ".", ".."}:
            return "is not a name"
        if self.windows and ":" in name:
            return "contains ':' (Windows reads `name:stream` as an alternate data stream)"
        forbidden = sorted(filter(self.forbidden, set(name)))
        if forbidden:
            return f"contains characters not allowed in a name: {forbidden!r}"
        if self.length(name) > MAX_NAME_LENGTH:
            return f"is longer than {MAX_NAME_LENGTH}"
        return _windows_problem(name) if self.windows else None
