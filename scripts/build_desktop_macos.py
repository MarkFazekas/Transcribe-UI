"""The macOS floor of a desktop build: no bundled binary may need a newer macOS than the release supports.

Used by `scripts/build_desktop.py` after a macOS build (docs/ai/plans/m8.md §1 "Platforms and
runners": macOS 14 or later). PyInstaller's package table of contents (`PKG-00.toc` in its work
folder) names the source of every binary and extension it bundled; `otool -l` reads each one's
minimum macOS (`minos` of `LC_BUILD_VERSION`, or `version` of the older `LC_VERSION_MIN_MACOSX`,
for every architecture of a universal file). An interpreter or wheel built for a newer macOS
would otherwise only fail on a user's older system.
"""

import ast
import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

# A macOS version as numbers, such as (14, 0).
type MacosVersion = tuple[int, ...]

# The oldest macOS the release supports (the PySide6 6.12 wheel needs 14).
MACOS_FLOOR: MacosVersion = (14, 0)
# Table-of-contents types of the bundled Mach-O files.
BINARY_TYPES = frozenset(("BINARY", "EXTENSION"))
# The minimum macOS in `otool -l` output: `minos 14.0` or, in older binaries, `version 10.13`.
MINIMUM_LINE = re.compile(r"^\s*(?:minos|version)\s+(\d+(?:\.\d+)*)\s*$", re.MULTILINE)
# Load commands whose `version` line is a minimum macOS.
MINIMUM_COMMANDS = ("LC_BUILD_VERSION", "LC_VERSION_MIN_MACOSX")
# Seconds `otool` may take for one file.
OTOOL_TIMEOUT_S = 60


def minimum_versions(otool_output: str) -> list[MacosVersion]:
    """Return the minimum macOS of each architecture in `otool -l` output (none: it names none)."""
    versions: list[MacosVersion] = []
    commands = otool_output.split("Load command")[1:]
    for command in filter(_names_minimum, commands):
        found = MINIMUM_LINE.search(command)
        if found is not None:
            parts = found.group(1).split(".")
            versions.append(tuple(int(part) for part in parts))
    return versions


def _names_minimum(command: str) -> bool:
    return any(f"cmd {name}" in command for name in MINIMUM_COMMANDS)


def bundled_binaries(table_of_contents: Path) -> list[Path]:
    """Return the source of every binary and extension in PyInstaller's package table of contents."""
    # The table is a Python literal: (package name, options, [(name, source, type), ...]).
    entries = ast.literal_eval(table_of_contents.read_text(encoding="utf-8"))[2]
    sources = {source for _name, source, kind in entries if kind in BINARY_TYPES}
    return sorted(map(Path, sources))


def _format(version: MacosVersion) -> str:
    return ".".join(map(str, version))


def floor_problems(binaries: Sequence[Path], floor: MacosVersion = MACOS_FLOOR) -> list[str]:
    """Return every binary that needs a newer macOS than `floor` (or that `otool` cannot read)."""
    otool = shutil.which("otool") or "otool"
    problems = []
    for binary in binaries:
        read = subprocess.run(
            [otool, "-l", str(binary)],
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
