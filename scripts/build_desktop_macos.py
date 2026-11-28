"""The macOS floor of a desktop build: no bundled binary may need a newer macOS than the release supports.

Used by `scripts/build_desktop.py` after a macOS build (docs/ai/plans/m8.md §1 "Platforms and
runners": macOS 14 or later). PyInstaller's package table of contents (`PKG-00.toc` in its work
folder) names the source of every binary and extension it bundled; `otool -arch all -l` reads
each one's minimum macOS for every architecture of a universal file (`minos` of
`LC_BUILD_VERSION`, or `version` of the older `LC_VERSION_MIN_MACOSX`; only those, never the
linker's `version` that `LC_BUILD_VERSION` also lists). An interpreter or wheel built for a newer macOS
would otherwise only fail on a user's older system.
"""

import ast
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType

# A macOS version as numbers, such as (14, 0).
type MacosVersion = tuple[int, ...]

# The oldest macOS the release supports (the PySide6 6.12 wheel needs 14).
MACOS_FLOOR: MacosVersion = (14, 0)
# Table-of-contents types of the bundled Mach-O files.
BINARY_TYPES = frozenset(("BINARY", "EXTENSION"))
# The line naming a load command in `otool -l` output.
COMMAND_LINE = re.compile(r"^\s*cmd\s+(\S+)\s*$", re.MULTILINE)
# The minimum macOS line of each load command that has one: `minos 14.0` of the build version
# command (whose tool entries also have `version` lines), `version 10.13` of the older one.
MINIMUM_LINES: Mapping[str, re.Pattern[str]] = MappingProxyType(
    {
        "LC_BUILD_VERSION": re.compile(r"^\s*minos\s+(\d+(?:\.\d+)*)\s*$", re.MULTILINE),
        "LC_VERSION_MIN_MACOSX": re.compile(r"^\s*version\s+(\d+(?:\.\d+)*)\s*$", re.MULTILINE),
    },
)
# Options of `otool`: every architecture of a universal file, then the load commands.
OTOOL_OPTIONS = ("-arch", "all", "-l")
# Seconds `otool` may take for one file.
OTOOL_TIMEOUT_S = 60


def minimum_versions(otool_output: str) -> list[MacosVersion]:
    """Return the minimum macOS of each architecture in `otool -l` output (none: it names none)."""
    versions: list[MacosVersion] = []
    for command in otool_output.split("Load command")[1:]:
        found = _minimum(command)
        if found is not None:
            versions.append(found)
    return versions


def _minimum(command: str) -> MacosVersion | None:
    """Return the minimum macOS one load command names, or None when it is not such a command."""
    named = COMMAND_LINE.search(command)
    if named is None:
        return None
    pattern = MINIMUM_LINES.get(named.group(1))
    found = None if pattern is None else pattern.search(command)
    if found is None:
        return None
    parts = found.group(1).split(".")
    return tuple(int(part) for part in parts)


def bundled_binaries(table_of_contents: Path) -> list[Path]:
    """Return the source of every binary and extension in PyInstaller's package table of contents."""
    # The table is a Python literal: (package name, options, [(name, source, type), ...]).
    entries = ast.literal_eval(table_of_contents.read_text(encoding="utf-8"))[2]
    sources = {source for _name, source, kind in entries if kind in BINARY_TYPES}
    return sorted(map(Path, sources))


def _format(version: MacosVersion) -> str:
    return ".".join(map(str, version))


def floor_problems(binaries: Sequence[Path], floor: MacosVersion = MACOS_FLOOR) -> list[str]:
    """Return every binary that needs a newer macOS than `floor` (or that `otool` cannot read).

    Raises:
        subprocess.SubprocessError: `otool` did not finish in time.
    """
    otool = shutil.which("otool")
    if otool is None:
        return ["otool not found"]
    problems = []
    for binary in binaries:
        read = subprocess.run(
            [otool, *OTOOL_OPTIONS, str(binary)],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=OTOOL_TIMEOUT_S,
            check=False,
        )
        if read.returncode:
            problems.append(f"{binary}: otool failed: {read.stderr.strip()}")
            continue
        newest = max(minimum_versions(read.stdout), default=floor)
        if newest > floor:
            needed, supported = _format(newest), _format(floor)
            problems.append(f"{binary} needs macOS {needed} (the floor is {supported})")
    return problems
