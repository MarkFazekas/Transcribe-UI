"""The installed wheels a desktop build bundles from, and the macOS floor their tags state.

Used by `scripts/build_desktop_macos.py`. A wheel's `WHEEL` file names its tags
(`Tag: cp310-abi3-macosx_14_0_universal2`); the macOS version in a `macosx_<major>_<minor>`
tag is the oldest macOS the wheel supports (a compressed tag set such as
`macosx_10_9_x86_64.macosx_15_0_arm64` counts with its highest). Its `RECORD` file lists every
file it installed, which maps a bundled file to its wheel by the file's resolved path
(PyInstaller's table of contents keeps the original source paths), so a file from anywhere else
never gets a wheel's tag.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib.metadata import Distribution, distributions
from pathlib import Path

# A macOS version as numbers, such as (14, 0).
type MacosVersion = tuple[int, ...]
# The wheel of each installed file, by its resolved path.
type Owners = Mapping[str, Wheel]

# The macOS version of a platform tag, such as 14.0 of a wheel tagged for macOS 14.
MACOS_TAG = re.compile(r"macosx_(\d+)_(\d+)_")
# Prefix of a tag line in a `WHEEL` file.
TAG_PREFIX = "Tag:"


@dataclass(frozen=True)
class Wheel:
    """An installed wheel: its name, version and tags."""

    name: str
    version: str
    tags: tuple[str, ...]

    def macos_floor(self) -> MacosVersion | None:
        """Return the oldest macOS the wheel's tags state (the highest of its `macosx` tags), or None."""
        found = [match for tag in self.tags for match in MACOS_TAG.finditer(tag)]
        return max(map(_version, found), default=None)


def _version(tag: re.Match[str]) -> MacosVersion:
    major, minor = tag.groups()
    return int(major), int(minor)


def wheel_tags(wheel_text: str) -> tuple[str, ...]:
    """Return the tags a `WHEEL` file lists."""
    lines = [line.strip() for line in wheel_text.splitlines()]
    tag_lines = [line for line in lines if line.startswith(TAG_PREFIX)]
    return tuple(line.removeprefix(TAG_PREFIX).strip() for line in tag_lines)


def _wheel(distribution: Distribution) -> Wheel:
    tags = wheel_tags(distribution.read_text("WHEEL") or "")
    return Wheel(distribution.metadata["Name"], distribution.version, tags)


def installed_wheels(installed: Iterable[Distribution] | None = None) -> dict[str, Wheel]:
    """Return the wheel of every file the distributions installed (default: this interpreter's).

    Each file is a key by its resolved path.
    """
    owners: dict[str, Wheel] = {}
    for distribution in distributions() if installed is None else installed:
        wheel = _wheel(distribution)
        for installed_file in distribution.files or ():
            location = Path(str(distribution.locate_file(installed_file)))
            owners[str(location.resolve())] = wheel
    return owners


def owner(owners: Owners, source: Path) -> Wheel | None:
    """Return the wheel that installed the bundled file collected from `source`, or None."""
    return owners.get(str(source.resolve()))
