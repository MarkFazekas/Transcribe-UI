"""The macOS floor of a desktop build: nothing bundled may need a newer macOS than the release supports.

Used by `scripts/build_desktop.py` after a macOS build (docs/ai/plans/m8.md §1 "Platforms and
runners": macOS 14 or later). PyInstaller's package table of contents (`PKG-00.toc` in its work
folder) names every binary and extension it bundled, with its original source; the executable
itself (PyInstaller's bootloader, which `EXE` adds separately) is read from `dist/`. Two rules:

- A binary from an installed wheel follows the wheel's platform tag (`macosx_14_0` passes, a
  higher one fails). The `minos` of a wheel's own binaries is no floor signal: Qt's wheels
  regularly carry a higher one than their tag (shiboken6 6.12.0, tagged `macosx_14_0`, has
  `minos 26.0`; its Qt libraries 14.4), so it is only reported, per wheel, for information.
- Everything else must have a minimum macOS of at most the floor: the executable (the one
  Mach-O whose minimum macOS macOS enforces at start; never exempted by the PyInstaller
  wheel's tag), python-build-standalone's libpython and extensions, a binary of a wheel without
  a `macosx` tag, later the ffmpeg this repository builds. It is read with
  `otool -arch all -l` for every architecture of a universal file (`minos` of
  `LC_BUILD_VERSION`, or `version` of the older `LC_VERSION_MIN_MACOSX`; only those, never the
  linker's `version` that `LC_BUILD_VERSION` also lists).

The GUI is a onedir `.app` (`check_app`): every Mach-O file inside the bundle (found by its
first bytes, links left out) follows the same two rules, its source looked up by its bundled
name in the table of contents of PyInstaller's `BUNDLE` (`BUNDLE-00.toc`). A file the table
does not name follows the minimum-macOS rule, so a lookup that misses fails the build rather
than passing a binary; the bundle's main executable follows that rule too.
"""

import ast
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from types import MappingProxyType

from build_desktop_bundle import app_executable, bundle_files, bundled_name, is_mach_o
from build_desktop_wheels import MacosVersion, Owners, Wheel, installed_wheels, owner

# Reads the minimum macOS of each architecture of a binary.
type MinimumReader = Callable[[Path], list[MacosVersion]]

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
# What reading a table of contents that is not PyInstaller's literal raises (`literal_eval`, indexing, unpacking).
TABLE_ERRORS = (SyntaxError, ValueError, LookupError, TypeError)


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


@dataclass(frozen=True)
class Bundled:
    """A binary in the bundle: its name inside the bundle and the file it was collected from."""

    name: str
    source: Path


@dataclass(frozen=True)
class FloorReport:
    """What breaks the floor, and one line per wheel with binaries (its tags and highest `minos`)."""

    problems: tuple[str, ...]
    wheels: tuple[str, ...]


class OtoolError(RuntimeError):
    """`otool` could not read a binary."""


def bundled_binaries(table_of_contents: Path) -> list[Bundled]:
    """Return every binary and extension in PyInstaller's package table of contents."""
    # The table is a Python literal: (package name, options, [(name, source, type), ...]).
    entries = ast.literal_eval(table_of_contents.read_text(encoding="utf-8"))[2]
    binaries = [entry for entry in entries if entry[2] in BINARY_TYPES]
    found = {_bundled(*entry) for entry in binaries}
    return sorted(found, key=_bundle_name)


def _bundled(name: str, source: str, _kind: str) -> Bundled:
    return Bundled(name, Path(source))


def _bundle_name(binary: Bundled) -> str:
    return binary.name


def _read_with(otool: str, binary: Path) -> list[MacosVersion]:
    """Return the minimum macOS of each architecture of `binary`.

    Raises:
        OtoolError: `otool` failed.
        subprocess.SubprocessError: `otool` did not finish in time.
    """
    read = subprocess.run(
        [otool, *OTOOL_OPTIONS, str(binary)],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=OTOOL_TIMEOUT_S,
        check=False,
    )
    if read.returncode:
        msg = f"{binary}: otool failed: {read.stderr.strip()}"
        raise OtoolError(msg)
    return minimum_versions(read.stdout)


def otool_reader() -> MinimumReader | None:
    """Return the reader of minimum macOS versions through `otool`, or None when there is no `otool`."""
    otool = shutil.which("otool")
    return None if otool is None else partial(_read_with, otool)


def _format(version: MacosVersion) -> str:
    return ".".join(map(str, version))


def _wheel_line(wheel: Wheel, newest: MacosVersion) -> str:
    tags = ", ".join(wheel.tags)
    minos = _format(newest)
    return f"wheel {wheel.name} {wheel.version}: tags {tags}; highest minos {minos}"


def _too_new(name: str, newest: MacosVersion, floor: MacosVersion) -> str:
    return f"{name} needs macOS {_format(newest)} (the floor is {_format(floor)})"


def _wheel_problems(highest: Mapping[Wheel, MacosVersion], floor: MacosVersion) -> list[str]:
    problems = []
    for wheel in highest:
        tagged = wheel.macos_floor() or floor
        if tagged > floor:
            needed = _format(tagged)
            problems.append(f"wheel {wheel.name} {wheel.version} is tagged for macOS {needed}")
    return problems


def floor_report(
    binaries: Sequence[Bundled],
    owners: Owners,
    read: MinimumReader,
    floor: MacosVersion = MACOS_FLOOR,
) -> FloorReport:
    """Check every binary against the floor: by its wheel's tag, else by its own minimum macOS.

    Raises:
        OtoolError: A binary could not be read.
        subprocess.SubprocessError: Reading a binary did not finish in time.
    """
    problems: list[str] = []
    highest: dict[Wheel, MacosVersion] = {}
    for binary in binaries:
        newest = max(read(binary.source), default=(0,))
        wheel = owner(owners, binary.source)
        if wheel is not None and wheel.macos_floor() is not None:
            highest[wheel] = max(newest, highest.get(wheel, newest))
        elif newest > floor:
            problems.append(_too_new(binary.name, newest, floor))
    problems.extend(_wheel_problems(highest, floor))
    lines = sorted(_wheel_line(*wheel_minos) for wheel_minos in highest.items())
    return FloorReport(tuple(problems), tuple(lines))


def executable_problems(executable: Path, read: MinimumReader, floor: MacosVersion = MACOS_FLOOR) -> list[str]:
    """Return the problem of the built executable (the bootloader): a newer macOS than `floor`, or none named."""
    versions = read(executable)
    if not versions:
        return [f"{executable.name}: no minimum macOS found"]
    newest = max(versions)
    return [_too_new(executable.name, newest, floor)] if newest > floor else []


def check_bundle(table_of_contents: Path, executable: Path) -> FloorReport:
    """Check the built executable and what PyInstaller bundled (its package table of contents) against the floor.

    Raises:
        OtoolError: A binary could not be read.
        subprocess.SubprocessError: Reading a binary did not finish in time.
    """
    read = otool_reader()
    if read is None:
        return FloorReport(("otool not found",), ())
    report = floor_report(bundled_binaries(table_of_contents), installed_wheels(), read)
    problems = (*executable_problems(executable, read), *report.problems)
    return FloorReport(problems, report.wheels)


def bundle_sources(table_of_contents: Path) -> dict[str, Path]:
    """Return the source of every file in the table of contents of PyInstaller's `BUNDLE`, by its bundled name.

    Raises:
        SyntaxError, ValueError, LookupError, TypeError: The table is not PyInstaller 6's literal
            (`TABLE_ERRORS`: another PyInstaller, a damaged file).
    """
    # The table is a Python literal: ([(name, source, type), ...],).
    entries = ast.literal_eval(table_of_contents.read_text(encoding="utf-8"))[0]
    return {name: Path(source) for name, source, _kind in entries}


def app_binaries(app: Path, sources: Mapping[str, Path], executable: Path) -> list[Bundled]:
    """Return every Mach-O file of the app but its main executable, with its source (else the file itself)."""
    contents_dir = app / "Contents"
    found = []
    for relative in bundle_files(app):
        path = contents_dir / relative
        if path == executable or not is_mach_o(path):
            continue
        name = bundled_name(relative)
        source = None if name is None else sources.get(name)
        found.append(Bundled(relative.as_posix(), source or path))
    return found


def check_app(table_of_contents: Path, app: Path) -> FloorReport:
    """Check the app's main executable and every other Mach-O file in it against the floor.

    Raises:
        OtoolError: A binary could not be read.
        subprocess.SubprocessError: Reading a binary did not finish in time.
        ValueError: The app's `Info.plist` names no executable.
    """
    read = otool_reader()
    if read is None:
        return FloorReport(("otool not found",), ())
    try:
        sources = bundle_sources(table_of_contents)
    except TABLE_ERRORS as error:
        problem = f"{table_of_contents}: not a table of contents this check can read ({error!r})"
        return FloorReport((problem,), ())
    executable = app_executable(app)
    binaries = app_binaries(app, sources, executable)
    report = floor_report(binaries, installed_wheels(), read)
    problems = (*executable_problems(executable, read), *report.problems)
    return FloorReport(problems, report.wheels)
