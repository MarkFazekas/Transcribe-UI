"""Runs PyInstaller with the shared libraries the Linux build leaves out and the third-party notices (docs/ai/plans/m8.md).

`scripts/build_desktop.py` starts this file with the build environment's interpreter and the
PyInstaller command line. PyInstaller has no command-line option to leave out a shared library
its dependency analysis finds (only a `.spec` file can filter its binaries), so on Linux this
adds `libstdc++` and `libgcc_s` to PyInstaller's exclude list first: a copy from the build
container is older than the one the host's graphics driver (Mesa's LLVM) needs and would shadow
it, and every Linux system has both.

Third-party notices (M8.3): when `TRANSCRIBE_UI_NOTICES_CONFIG` names the JSON file
`scripts/build_desktop.py` wrote, PyInstaller's `Analysis` is replaced by a subclass that, once
the analysis is done, hands its table of contents to `scripts/build_desktop_facts.py`, which
writes the notices of what was collected, and adds that file to the bundle's data. The scripts
folder joins the module search path only then, so it cannot change what is collected. Missing
licence entries end the build with exit code 1, naming each.
"""

import importlib
import json
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, cast

from PyInstaller import __main__ as pyinstaller
from PyInstaller.building import build_main

# An entry of PyInstaller's table of contents: the name in the bundle, the source file, the type code.
type Entry = tuple[str, str | None, str | None]

# Environment variable naming the notices configuration (`scripts/build_desktop.py`).
NOTICES_VARIABLE = "TRANSCRIBE_UI_NOTICES_CONFIG"
# The module of `scripts/` that writes the notices.
NOTICES_MODULE = "build_desktop_facts"
# Exit code of a build whose notices are incomplete.
NOTICES_FAILED = 1
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


class NoticesModule(Protocol):
    """What this runner uses of `scripts/build_desktop_facts.py`."""

    def notices_entry(self, config_path: Path, collected: Sequence[Entry]) -> tuple[str, str]:
        """Write the notices of `collected`; return the file's name in the bundle and its path."""
        ...


def add_notices(analysis: build_main.Analysis, config_path: Path) -> None:
    """Add the notices of what `analysis` collected to its data; exit 1 when they are incomplete."""
    config = json.loads(config_path.read_text(encoding="utf-8"))
    sys.path.append(str(config["scripts"]))
    module = cast("NoticesModule", importlib.import_module(NOTICES_MODULE))
    collected: list[Entry] = [*analysis.scripts, *analysis.pure]
    collected.extend((*analysis.binaries, *analysis.datas))
    try:
        name, path = module.notices_entry(config_path, collected)
    except RuntimeError as error:
        sys.stderr.write(f"{error}\n")
        sys.exit(NOTICES_FAILED)
    analysis.datas.append((name, path, "DATA"))


class Analysis(build_main.Analysis):
    """PyInstaller's analysis plus the third-party notices (named like it: its name names the work files)."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Analyse as PyInstaller does, then add the notices of what was collected."""
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]  # the spec's arguments, passed on unchanged
        add_notices(self, Path(os.environ[NOTICES_VARIABLE]))


if __name__ == "__main__":
    if sys.platform == "linux":
        dylib = cast("DylibModule", importlib.import_module("PyInstaller.depend.dylib"))
        # `dylib.include_library` reads this module attribute on each call.
        dylib.exclude_list = HostLibraries(dylib.exclude_list)
    if os.environ.get(NOTICES_VARIABLE):
        # The spec file's namespace takes `Analysis` from this module attribute when it runs.
        build_main.Analysis = Analysis  # type: ignore[misc]  # replaces the class the spec file is given
    pyinstaller.run()
