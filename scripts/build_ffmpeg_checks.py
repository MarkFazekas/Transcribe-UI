"""Checks of an ffmpeg build (docs/ai/plans/m8.md §1 "ffmpeg").

Used by `scripts/build_ffmpeg.py`. Every check is a pure function from a tool's text to the list of
problems it found (empty: none):

- the configure result in the build tree (`ffbuild/config.mak`) against the library lists of the
  pinned `configure`: the licence switches off, every external library but zlib off, zlib and the
  platform's thread library on, both programs on;
- the shared libraries the programs load (`ldd`, `otool -L` or `objdump -p`): only the OS's own;
- `-buildconf` of a built program against the expected configure line, and `-L` (LGPL 2.1 or later).
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType

# Names of libraries or switches.
type Names = Iterable[str]

# Lists of `configure` that together name every external library, hardware acceleration included.
LIBRARY_LISTS = (
    "EXTERNAL_LIBRARY_LIST",
    "EXTERNAL_AUTODETECT_LIBRARY_LIST",
    "HWACCEL_LIBRARY_LIST",
    "HWACCEL_AUTODETECT_LIBRARY_LIST",
)
# List of `configure` naming the thread libraries (`pthreads`, `w32threads`, `os2threads`).
THREADS_LIST = "THREADS_LIST"
# A list assignment of `configure`: `NAME="` items separated by whitespace `"`.
LIST_ASSIGNMENT = re.compile(r'^([A-Z0-9_]+)="([^"]*)"', re.MULTILINE)
# A name in such a list.
LIST_NAME = re.compile(r"[A-Za-z0-9_]+")
# Prefix of an item of such a list that stands for another list's items (`$EXTERNAL_LIBRARY_GPL_LIST`).
LIST_REFERENCE = "$"
# A switch of `config.mak` that is on (`NAME=yes`; one that is off reads `!NAME=yes`).
SWITCH_ON = re.compile(r"([A-Z0-9_]+)=yes")
# The licence switches, each of which must be off for LGPL 2.1 or later.
LICENCE_SWITCHES = ("gpl", "nonfree", "version3")
# The programs the build must have.
PROGRAMS = ("ffmpeg", "ffprobe")
# The one external library every build uses.
ZLIB = "zlib"
# The libraries of glibc a Linux build may load (and the kernel's virtual one).
GLIBC_LIBRARIES = re.compile(r"(libc|libm|libpthread|libdl|librt)\.so\.\d+|ld-linux-x86-64\.so\.2|linux-vdso\.so\.1")
# Folders of the libraries and frameworks every macOS has.
MACOS_SYSTEM_FOLDERS = ("/usr/lib/", "/System/")
# DLLs of Windows itself a Windows build may import (lower case); no mingw runtime.
WINDOWS_SYSTEM_DLLS = frozenset(
    (
        "advapi32.dll",
        "bcrypt.dll",
        "gdi32.dll",
        "kernel32.dll",
        "msvcrt.dll",
        "ole32.dll",
        "psapi.dll",
        "shell32.dll",
        "shlwapi.dll",
        "user32.dll",
        "ucrtbase.dll",
    ),
)
# Prefix of the API sets of the Universal C runtime, which are part of Windows.
WINDOWS_API_SET = "api-ms-win-"
# The line of `objdump -p` that names an imported DLL.
DLL_NAME = "DLL Name:"
# How every configure argument starts.
ARGUMENT_PREFIX = "--"
# The space before another argument on one line of `-buildconf`.
ARGUMENT_BREAK = re.compile(r" (?=--)")
# The line `-buildconf` prints before the configure arguments.
CONFIGURATION_HEADING = "configuration:"
# The licence `-L` prints for an LGPL 2.1 or later build (whitespace normalised).
LGPL_NOTICE = (
    "under the terms of the GNU Lesser General Public License as published by the Free Software "
    "Foundation; either version 2.1 of the License, or (at your option) any later version."
)
# Platforms by the tool that lists the libraries of a program.
LIBRARY_TOOLS: Mapping[str, str] = MappingProxyType({"linux": "ldd", "macos": "otool -L", "windows": "objdump -p"})


@dataclass(frozen=True)
class Expansion:
    """The names a `configure` list holds and the items of it that could not be read."""

    names: set[str]
    problems: list[str]

    def merge(self, other: "Expansion") -> None:
        """Add another expansion's names and problems."""
        self.names.update(other.names)
        self.problems.extend(other.problems)


def configure_lists(configure: str) -> dict[str, tuple[str, ...]]:
    """Return every list assignment of a `configure` script by name, its items as written."""
    assignments = LIST_ASSIGNMENT.findall(configure)
    return {name: tuple(body.split()) for name, body in assignments}


def expand(name: str, lists: Mapping[str, Sequence[str]]) -> Expansion:
    """Return the names a list holds, its `$NAME` items expanded, and the items that could not be read."""
    entries = lists.get(name, ())
    nested = [entry.removeprefix(LIST_REFERENCE) for entry in entries if entry.startswith(LIST_REFERENCE)]
    plain = [entry for entry in entries if not entry.startswith(LIST_REFERENCE)]
    unread = [entry for entry in plain if LIST_NAME.fullmatch(entry) is None]
    unknown = [other for other in nested if other not in lists]
    unread.extend(f"{LIST_REFERENCE}{other}" for other in unknown)
    expansion = Expansion(set(plain) - set(unread), [])
    expansion.problems.extend(f"configure: cannot read the item {entry!r} of {name}" for entry in unread)
    for other in nested:
        if other in lists:
            expansion.merge(expand(other, lists))
    return expansion


def switched_on(config_mak: str) -> frozenset[str]:
    """Return the switches `config.mak` sets on (`CONFIG_ZLIB`, `HAVE_PTHREADS`, ...); `!NAME=yes` is off."""
    found = (SWITCH_ON.fullmatch(line.strip()) for line in config_mak.splitlines())
    return frozenset(match.group(1) for match in found if match)


def switch_problems(on: frozenset[str], wanted: Mapping[str, bool]) -> list[str]:
    """Return a problem for each switch that is on but not wanted, or wanted but off (in `wanted`'s order)."""
    problems = []
    for switch, state in wanted.items():
        found = switch in on
        if found != state:
            problems.append(f"config.mak: {switch} is {'on' if found else 'off'}")
    return problems


def _wanted(prefix: str, names: Names, allowed: Names) -> dict[str, bool]:
    """Return `<prefix><NAME>` of each name (sorted) and whether it must be on."""
    wanted = {}
    for name in sorted(names):
        wanted[f"{prefix}{name.upper()}"] = name in allowed
    return wanted


def build_tree_problems(configure: str, config_mak: str, thread: str) -> list[str]:
    """Return what the build tree's configure result enables or lacks against the allow-list.

    `configure` is the pinned release's script (its library lists), `config_mak` the build
    tree's `ffbuild/config.mak`, `thread` the platform's thread library (`pthreads`, `w32threads`).
    """
    lists = configure_lists(configure)
    libraries = Expansion(set(), [])
    for name in LIBRARY_LISTS:
        libraries.merge(expand(name, lists))
    threads = expand(THREADS_LIST, lists)
    problems = [*libraries.problems, *threads.problems]
    if ZLIB not in libraries.names or thread not in threads.names:
        problems.append("configure: the library lists do not name zlib and the thread library")
    groups = (
        _wanted("CONFIG_", libraries.names, (ZLIB,)),
        _wanted("HAVE_", threads.names, (thread,)),
        _wanted("CONFIG_", LICENCE_SWITCHES, ()),
        _wanted("CONFIG_", PROGRAMS, PROGRAMS),
    )
    on = switched_on(config_mak)
    for wanted in groups:
        problems.extend(switch_problems(on, wanted))
    return problems


def linked_libraries(system: str, listing: str) -> list[str]:
    """Return the libraries a program loads, from the output of the system's tool (`LIBRARY_TOOLS`).

    Linux (`ldd`): the file names; macOS (`otool -L`): the install names; Windows
    (`objdump -p`): the imported DLLs.
    """
    lines = [line.strip() for line in listing.splitlines()]
    if system == "windows":
        imports = [line for line in lines if line.startswith(DLL_NAME)]
        return [line.removeprefix(DLL_NAME).strip() for line in imports]
    if system == "macos":
        # The first line names the program itself.
        return [line.split()[0] for line in lines[1:] if line]
    return [PurePosixPath(line.split()[0]).name for line in lines if line]


def _is_system_library(system: str, library: str) -> bool:
    if system == "windows":
        lowered = library.lower()
        return lowered in WINDOWS_SYSTEM_DLLS or lowered.startswith(WINDOWS_API_SET)
    if system == "macos":
        return library.startswith(MACOS_SYSTEM_FOLDERS)
    return GLIBC_LIBRARIES.fullmatch(library) is not None


def dependency_problems(program: str, system: str, libraries: Iterable[str]) -> list[str]:
    """Return the libraries of a program that are not the OS's own (`system`: linux, macos or windows)."""
    listed = list(libraries)
    if not listed:
        return [f"{program}: {LIBRARY_TOOLS[system]} lists no library"]
    foreign = [library for library in listed if not _is_system_library(system, library)]
    return [f"{program} loads {library}" for library in foreign]


def buildconf_arguments(output: str) -> list[str]:
    """Return the configure arguments `-hide_banner -buildconf` printed (one per line after its heading).

    ffmpeg prints the argument after one that ends in `pkg-config` on the same line (meant for
    `--pkg-config="pkg-config --static"`); no argument here holds a space, so such a line is split
    again. ffmpeg ends its output with an `Exiting with exit code 0` line, which is no argument.
    """
    lines = [line.strip() for line in output.splitlines()]
    if CONFIGURATION_HEADING not in lines:
        return []
    start = lines.index(CONFIGURATION_HEADING) + 1
    shown = [line for line in lines[start:] if line.startswith(ARGUMENT_PREFIX)]
    return [argument for line in shown for argument in ARGUMENT_BREAK.split(line)]


def buildconf_problems(program: str, arguments: Sequence[str], expected: Sequence[str]) -> list[str]:
    """Return how a program's configure arguments differ from the expected configure line."""
    if list(arguments) == list(expected):
        return []
    missing = [argument for argument in expected if argument not in arguments]
    extra = [argument for argument in arguments if argument not in expected]
    problems = [f"{program} -buildconf lacks {argument}" for argument in missing]
    problems.extend(f"{program} -buildconf has {argument}" for argument in extra)
    return problems or [f"{program} -buildconf has the expected arguments in another order"]


def licence_problems(program: str, output: str) -> list[str]:
    """Return a problem unless `-L` printed the LGPL 2.1 or later notice."""
    if LGPL_NOTICE in " ".join(output.split()):
        return []
    return [f"{program} -L does not report LGPL 2.1 or later"]
