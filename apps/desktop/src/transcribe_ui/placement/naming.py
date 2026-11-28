"""Target-OS name rules of the placement planner (architecture §3.5).

The rules follow the injected path-flavour rule (`discovery/rules.py`), so the Windows rules are
tested on any OS: generated names are made valid (invalid characters to `_`, reserved device
names and trailing dots or spaces avoided), imported names are checked and refused.
"""

import ntpath
import posixpath
from dataclasses import dataclass, field
from pathlib import PurePath, PurePosixPath, PureWindowsPath

from pydantic import JsonValue

from transcribe_contracts.classes import PathFlavor
from transcribe_ui.discovery.rules import PathRules
from transcribe_ui.placement import constants

# Rule keys of a path, part by part: equal keys are the same path.
type PathKey = tuple[str, ...]


def split_name(file_name: str) -> tuple[str, str]:
    """Return a file name's stem and its extension without the dot (empty when it has none)."""
    stem, extension = posixpath.splitext(file_name)
    return stem, extension.removeprefix(".")


def subtitle_name(stem: str, language: str) -> str:
    """Return the subtitle name `<stem>.<lang>.srt`."""
    return f"{stem}.{language}.{constants.SUBTITLE_EXTENSION}"


def has_subtitle_extension(file_name: str) -> bool:
    """Return whether a file name ends with `.srt` (in any case)."""
    return file_name.lower().endswith(f".{constants.SUBTITLE_EXTENSION}")


def is_clean_absolute(path: PurePath) -> bool:
    """Return whether a path is absolute and has no `..` part (so it cannot leave a folder)."""
    return path.is_absolute() and ".." not in path.parts


def unsafe_in(part: str, flavor: PathFlavor) -> bool:
    """Return whether a part below the scan root is no single file or folder name in that path flavour.

    Empty, `.` and `..` parts, a NUL, the flavour's separators, and a drive or an anchor (`C:x`)
    are refused; every other character a generated name later sanitises (architecture §3.5).
    """
    if part in constants.NOT_NAMES or constants.NUL in part:
        return True
    if any(separator in part for separator in constants.FLAVOR_SEPARATORS[flavor]):
        return True
    pure = PureWindowsPath(part) if flavor == "windows" else PurePosixPath(part)
    return bool(pure.anchor)


def unsafe_part(part: str) -> bool:
    """Return whether a part read from another computer's manifest is no single name on every OS (adoption)."""
    return unsafe_in(part, "windows") or unsafe_in(part, "posix")


def target_names(rules: "PathRules | TargetNames") -> "TargetNames":
    """Return the names of a target given by its rules (sources in its flavour) or by names (sources in theirs)."""
    return rules if isinstance(rules, TargetNames) else TargetNames(rules)


def sources_flavor(names: "TargetNames") -> PathFlavor:
    """Return the path flavour of the sources and the scan root."""
    return names.source_flavor or names.rules.flavor


def source_pure(names: "TargetNames", path: str) -> PurePath:
    """Return a source path (or the scan root) as a pure path of the batch's flavour."""
    return PureWindowsPath(path) if sources_flavor(names) == "windows" else PurePosixPath(path)


def _reserved(name: str) -> bool:
    # Windows reserves a device name also with an extension and with spaces before the dot.
    base = name.split(".", 1)[0].rstrip(" ")
    return base.upper() in constants.WINDOWS_RESERVED_NAMES


def _with_trailing_replaced(name: str) -> str:
    kept = name.rstrip(constants.WINDOWS_TRAILING_CHARACTERS)
    return kept + constants.REPLACEMENT_CHARACTER * (len(name) - len(kept))


def _with_base_suffix(name: str) -> str:
    base, dot, rest = name.partition(".")
    return f"{base}{constants.REPLACEMENT_CHARACTER}{dot}{rest}"


def _shown_character(character: str) -> str:
    """Return a character as a text can show it: itself, or `U+0000` for a control character."""
    return character if character.isprintable() else f"U+{ord(character):04X}"


@dataclass(frozen=True)
class NameProblem:
    """Why a user-given name is not valid on the target: a code, its values and the English text."""

    # One of the `NAME_PROBLEM_*` codes.
    code: str
    # What the name lacks, completing "the name ... " (English, for the message).
    text: str
    # The values the text shows (`characters`, `max_length`).
    details: dict[str, JsonValue] = field(default_factory=dict)


def _windows_problem(name: str) -> NameProblem | None:
    if name.endswith(tuple(constants.WINDOWS_TRAILING_CHARACTERS)):
        return NameProblem(constants.NAME_PROBLEM_TRAILING, "ends with a dot or a space")
    if _reserved(name):
        return NameProblem(constants.NAME_PROBLEM_RESERVED, "is a name Windows reserves for a device")
    return None


@dataclass(frozen=True)
class TargetNames:
    """Paths and names under the target OS's rules (the injected path-flavour rule).

    Source paths and the scan root are split in the batch's own path flavour (`source_flavor`:
    the OS that scanned it, another computer's for an adopted batch); None means the target's.
    """

    rules: PathRules
    source_flavor: PathFlavor | None = None

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
            return len(name.encode("utf-16-le", "surrogatepass")) // constants.UTF16_UNIT_BYTES
        return len(name.encode("utf-8", "surrogateescape"))

    def path_too_long(self, path: str) -> bool:
        """Return whether Windows cannot write the file: its temp file's path over MAX_PATH, or its folder too long to create."""
        if not self.windows:
            return False
        folder = str(self.pure(path).parent)
        too_long = self.length(path) + constants.TEMP_TAIL_LENGTH > constants.WINDOWS_MAX_PATH_LENGTH
        return too_long or self.length(folder) > constants.WINDOWS_MAX_FOLDER_LENGTH

    def forbidden(self, character: str) -> bool:
        """Return whether the target does not allow the character in a name."""
        if not self.windows:
            return character in constants.POSIX_FORBIDDEN_CHARACTERS
        control = ord(character) <= constants.WINDOWS_LAST_CONTROL_CHARACTER
        return control or character in constants.WINDOWS_FORBIDDEN_CHARACTERS

    def sanitised(self, name: str) -> str:
        """Return a generated file or folder name made valid for the target.

        Invalid characters become `_`; on Windows, trailing dots and spaces become `_` and a
        reserved device name gets `_` after its base (`CON.hu.srt` → `CON_.hu.srt`).
        """
        replaced = (constants.REPLACEMENT_CHARACTER if self.forbidden(character) else character for character in name)
        cleaned = "".join(replaced)
        if not self.windows:
            return cleaned
        cleaned = _with_trailing_replaced(cleaned)
        return _with_base_suffix(cleaned) if _reserved(cleaned) else cleaned

    def problem(self, name: str) -> NameProblem | None:
        """Return why a user-given file or folder name is not valid on the target, or None."""
        if name in {"", ".", ".."}:
            return NameProblem(constants.NAME_PROBLEM_NOT_A_NAME, "is not a name")
        if self.windows and ":" in name:
            text = "contains ':' (Windows reads `name:stream` as an alternate data stream)"
            return NameProblem(constants.NAME_PROBLEM_STREAM, text)
        forbidden = sorted(filter(self.forbidden, set(name)))
        if forbidden:
            text = f"contains characters not allowed in a name: {forbidden!r}"
            characters: list[JsonValue] = []
            characters.extend(map(_shown_character, forbidden))
            return NameProblem(constants.NAME_PROBLEM_CHARACTERS, text, {"characters": characters})
        if self.length(name) > constants.MAX_NAME_LENGTH:
            text = f"is longer than {constants.MAX_NAME_LENGTH}"
            return NameProblem(constants.NAME_PROBLEM_TOO_LONG, text, {"max_length": constants.MAX_NAME_LENGTH})
        return _windows_problem(name) if self.windows else None
