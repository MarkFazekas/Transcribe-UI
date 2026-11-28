"""Runs PyInstaller with the shared libraries the Linux build leaves out (docs/ai/plans/m8.md §1).

`scripts/build_desktop.py` starts this file with the build environment's interpreter and the
PyInstaller command line. PyInstaller has no command-line option to leave out a shared library
its dependency analysis finds (only a `.spec` file can filter its binaries), so on Linux this
adds `libstdc++` and `libgcc_s` to PyInstaller's exclude list first: a copy from the build
container is older than the one the host's graphics driver (Mesa's LLVM) needs and would shadow
it, and every Linux system has both. On other systems it runs PyInstaller unchanged.
"""

import importlib
import re
import sys
from pathlib import Path
from typing import Protocol, cast

from PyInstaller import __main__ as pyinstaller

# Shared libraries the Linux build never bundles: the C++ runtime and the GCC support library.
LINUX_EXCLUDED_LIBRARIES = re.compile(r"(libstdc\+\+|libgcc_s)\.so(\..*)?")


class LibraryList(Protocol):
    """PyInstaller's list of the shared libraries it never bundles (`dylib.exclude_list`)."""

    def check_library(self, library: str) -> object:
        """Return a true value when the library at the path `library` stays out of the bundle."""


class DylibModule(Protocol):
    """What this runner uses of `PyInstaller.depend.dylib`, which the PyInstaller types leave out."""

    exclude_list: LibraryList


class HostLibraries:
    """PyInstaller's exclude list plus the libraries of `LINUX_EXCLUDED_LIBRARIES`."""

    def __init__(self, excluded: LibraryList) -> None:
        """Extend `excluded`."""
        self._excluded = excluded

    def check_library(self, library: str) -> object:
        """Return a true value when the library at the path `library` stays out of the bundle."""
        if LINUX_EXCLUDED_LIBRARIES.fullmatch(Path(library).name):
            return True
        return self._excluded.check_library(library)


if __name__ == "__main__":
    if sys.platform == "linux":
        dylib = cast("DylibModule", importlib.import_module("PyInstaller.depend.dylib"))
        # `dylib.include_library` reads this module attribute on each call.
        dylib.exclude_list = HostLibraries(dylib.exclude_list)
    pyinstaller.run()
