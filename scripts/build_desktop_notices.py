"""Third-party notices of a desktop executable, made from what PyInstaller collected (docs/ai/plans/m8.md, M8.3).

The PyInstaller runner (`apps/desktop/packaging/pyinstaller_main.py`) hands over the analysis's
collected files of one variant once the analysis is done; `scripts/build_desktop_facts.py`
gathers the facts of the build machine. Every collected file is attributed, in this order, to:

1. the ffmpeg build (its folder): FFmpeg, and zlib (the static copy on Linux and Windows, the
   system's on macOS), with the pinned source URL and the URL the build downloaded it from;
2. Microsoft's C and C++ runtime libraries (Windows), by their file names;
3. an installed distribution, through the files its `RECORD` lists (the app's own
   distributions need no entry);
4. the interpreter (python-build-standalone): Python itself, and the libraries
   `build_desktop_licences.py` maps its modules and DLLs to; the libraries inside libpython
   come from its built-in modules;
5. the app's own build files (entry scripts, the stamp);
6. on Linux, the OS package that installed a shared library of the build system.

PyInstaller (its bootloader starts every executable) is always listed. A file that none of
these explains, an entry without a licence expression or without a licence text, or a licence
file that cannot be read fails the build. The data classes are in
`build_desktop_notices_classes.py`, the entries in `build_desktop_notices_entries.py` and the
text in `build_desktop_notices_render.py`. Standard library only.
"""

import os
from collections.abc import Iterable, Sequence
from pathlib import Path

from build_desktop_licences import (
    BUNDLED_LIBRARIES,
    CPYTHON_MODULES,
    MICROSOFT_RUNTIME_FILES,
    OWN_DISTRIBUTIONS,
    PYTHON_CORE_FILES,
    QT_OWN_BINARIES,
    QT_PLUGINS_FOLDER,
    QT_WHEELS,
    VENDORED_LIBRARIES,
    BundledLibrary,
)
from build_desktop_notices_classes import Attribution, BuildFacts, DistributionInfo
from build_desktop_notices_entries import build_notices
from build_desktop_notices_render import render

# An entry of PyInstaller's collected files: the name in the bundle, the source file, the kind.
type CollectedEntry = tuple[str, str | None, str | None]

# Kinds whose source is no file of its own (a link's target name, a file of another executable).
SKIPPED_KINDS = frozenset(("SYMLINK", "DEPENDENCY"))
# Kind of an extension module.
EXTENSION = "EXTENSION"
# Kind of any other shared library.
BINARY = "BINARY"
# Kinds of native code.
NATIVE_KINDS = frozenset((EXTENSION, BINARY))


class NoticesError(RuntimeError):
    """The notices cannot be complete: each problem names a file or an entry."""

    def __init__(self, problems: Sequence[str]) -> None:
        """Keep the problems; the message lists them."""
        self.problems = tuple(problems)
        listed = "\n".join(f"- {problem}" for problem in self.problems)
        super().__init__(f"The third-party notices are incomplete:\n{listed}")


def file_key(path: Path) -> str:
    """Return the key a file is looked up by (normalised, case-folded where the OS ignores case)."""
    return os.path.normcase(os.path.normpath(path))


def _is_inside(path: Path, folder: Path) -> bool:
    return file_key(path).startswith(file_key(folder) + os.sep)


def _library_of(module: str) -> BundledLibrary | None:
    return next((library for library in BUNDLED_LIBRARIES if module in library.modules), None)


def _add_builtin_libraries(facts: BuildFacts, found: Attribution) -> None:
    """Add the libraries inside libpython: those of the interpreter's built-in modules.

    A built-in module that is neither CPython's own nor in the library table is a problem: it
    may carry a library (Tcl/Tk, gdbm) no entry covers.
    """
    for module in sorted(facts.interpreter.builtin_modules):
        library = _library_of(module)
        if library is None:
            if module not in CPYTHON_MODULES:
                found.problems.append(f"the built-in module {module} of the interpreter has no licence entry")
        elif facts.platform in library.platforms:
            found.libraries.add(library.name)


def _extension_file(path: Path, facts: BuildFacts, found: Attribution) -> None:
    module = path.name.split(".")[0]
    library = _library_of(module)
    if library is None:
        if module not in CPYTHON_MODULES:
            found.problems.append(f"{path}: an extension module of the interpreter without a licence entry")
    elif facts.platform in library.platforms:
        found.libraries.add(library.name)


def _carries(library: BundledLibrary, file_name: str) -> bool:
    return library.files is not None and library.files.fullmatch(file_name) is not None


def _binary_file(path: Path, found: Attribution) -> None:
    carriers = [library for library in BUNDLED_LIBRARIES if _carries(library, path.name)]
    if not carriers:
        found.problems.append(f"{path}: a library of the interpreter without a licence entry")
    found.libraries.update(library.name for library in carriers)


def _interpreter_file(path: Path, kind: str | None, facts: BuildFacts, found: Attribution) -> None:
    """Attribute a file of the interpreter: Python, plus the library a module or DLL carries."""
    found.python = True
    if kind == EXTENSION:
        _extension_file(path, facts, found)
    elif kind == BINARY and not PYTHON_CORE_FILES.fullmatch(path.name):
        _binary_file(path, found)


def _distribution_of(path: Path, facts: BuildFacts) -> DistributionInfo | None:
    for candidate in (path, path.resolve()):
        dist = facts.distributions.get(file_key(candidate))
        if dist is not None:
            return dist
    return None


def _system_file(path: Path, facts: BuildFacts, found: Attribution) -> None:
    owner = None if facts.system is None else facts.system.owner(path)
    if owner is None:
        why = "" if facts.system is None else facts.system.last_failure()
        reason = f"; the package query: {why}" if why else ""
        found.problems.append(
            f"{path}: no licence entry (not from a distribution, the interpreter, the ffmpeg build or an OS package{reason})",
        )
        return
    found.packages.setdefault(owner, []).append(path.name)


def _vendored(path: Path, found: Attribution) -> bool:
    """Record a library a wheel vendors besides its own code; return whether the file is one."""
    for library in VENDORED_LIBRARIES:
        if library.files.fullmatch(path.name):
            found.vendored.setdefault(library.name, []).append(path.name)
            return True
    return False


def _is_qt_own(path: Path) -> bool:
    return QT_OWN_BINARIES.fullmatch(path.name) is not None or QT_PLUGINS_FOLDER in path.parts


def _distribution_file(path: Path, kind: str | None, dist: DistributionInfo, found: Attribution) -> None:
    """Attribute a file of a distribution; a binary of a Qt wheel must be Qt's own (Qt's licence covers only that)."""
    if dist.key in OWN_DISTRIBUTIONS:
        return
    found.distributions[dist.key] = dist
    if dist.key in QT_WHEELS and kind in NATIVE_KINDS and not _is_qt_own(path):
        found.problems.append(f"{path}: a binary of {dist.name} that is neither Qt's own nor a listed vendored library")


def _allowed_binary(path: Path, facts: BuildFacts) -> bool:
    """Return whether a binary comes from a folder a build may take binaries from."""
    build_folders = (facts.interpreter.prefix, facts.ffmpeg.folder)
    folders = (*facts.binary_folders, *build_folders, *facts.own_folders)
    return any(_is_inside(path, folder) for folder in folders)


def _attribute_file(path: Path, kind: str | None, facts: BuildFacts, found: Attribution) -> None:
    if kind in NATIVE_KINDS and not _allowed_binary(path, facts):
        found.problems.append(
            f"{path}: a binary from outside the build environment, the interpreter, the ffmpeg build and the OS's "
            "library folders (a tool folder on PATH?)",
        )
        return
    if _is_inside(path, facts.ffmpeg.folder):
        found.ffmpeg = True
        return
    if MICROSOFT_RUNTIME_FILES.fullmatch(path.name):
        found.microsoft = True
        return
    if _vendored(path, found):
        return
    dist = _distribution_of(path, facts)
    if dist is not None:
        _distribution_file(path, kind, dist, found)
        return
    if _is_inside(path, facts.interpreter.prefix):
        _interpreter_file(path, kind, facts, found)
    elif not any(_is_inside(path, folder) for folder in facts.own_folders):
        _system_file(path, facts, found)


def attribute(collected: Iterable[CollectedEntry], facts: BuildFacts) -> Attribution:
    """Return what the collected files contain; files without a licence entry are problems."""
    found = Attribution()
    _add_builtin_libraries(facts, found)
    for _name, source, kind in collected:
        if kind in SKIPPED_KINDS or not source:
            continue
        path = Path(source)
        # Relative sources are no files (a namespace package's `-`).
        if path.is_absolute():
            _attribute_file(path, kind, facts, found)
    return found


def generate(collected: Iterable[CollectedEntry], facts: BuildFacts) -> str:
    """Return the notices of the executable whose collected files are `collected`.

    Raises:
        NoticesError: A file has no licence entry, or an entry no expression or licence text.
    """
    notices, problems = build_notices(attribute(collected, facts), facts)
    if problems:
        raise NoticesError(problems)
    return render(facts.title, notices)
