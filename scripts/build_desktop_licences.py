"""What the third-party notices know beyond package metadata (docs/ai/plans/m8.md, M8.3).

Used by `scripts/build_desktop_notices.py`. Standard library only.

- The libraries python-build-standalone links into the interpreter, by the modules that use
  them: on Linux and macOS they are inside libpython (built-in modules), on Windows in the
  module's own `.pyd` or a DLL next to it. A library is listed where that platform's build
  carries its own copy, not where it links the OS's (macOS: zlib, ncurses, libedit).
- The extension modules of CPython that contain no third-party library, so a collected one
  needs no licence entry beyond Python's; any other extension module of the interpreter fails
  the build until it gets an entry here.
- Licence data a distribution's metadata lacks or states vaguely (PySide6 and Shiboken6 ship
  no licence file; PyInstaller's licence is a sentence).

The licence texts are committed in `apps/desktop/packaging/licences/`.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

# Root of the repository.
REPO_ROOT = Path(__file__).resolve().parent.parent
# Folder of the committed licence texts.
LICENCES_DIR = REPO_ROOT / "apps" / "desktop" / "packaging" / "licences"
# Where the repository, which holds the app's source and these build scripts, is published.
PROJECT_URL = "https://github.com/MarkFazekas/Transcribe-UI"
# The app's own distributions: the app itself, never a third party.
OWN_DISTRIBUTIONS = frozenset(("transcribe-ui", "transcribe-contracts"))
# The distribution of PyInstaller, whose bootloader starts every executable.
PYINSTALLER = "pyinstaller"
# Every platform of the release, as `sys.platform` names them.
ALL_PLATFORMS = frozenset(("linux", "darwin", "win32"))
# Python's own licence.
PYTHON_EXPRESSION = "PSF-2.0"
# CPython's own shared libraries and launch helpers (Windows `python313.dll`/`python3.dll`,
# `libpython3.13.so.1.0`, `libpython3.13.dylib`).
PYTHON_CORE_FILES = re.compile(r"(lib)?python3(\.?\d+)?(\.dll|\.so(\.[\d.]+)?|\.dylib)", re.IGNORECASE)
# Microsoft's C and C++ runtime libraries, wherever a wheel or the interpreter brings them.
MICROSOFT_RUNTIME_FILES = re.compile(
    r"(vcruntime140(_\d+)?|msvcp140(_\w+)?|concrt140|ucrtbase|api-ms-win-[\w-]+)\.dll",
    re.IGNORECASE,
)
# Extension modules of CPython without any third-party library in them.
CPYTHON_EXTENSIONS = frozenset(
    (
        "_asyncio",
        "_interpchannels",
        "_interpqueues",
        "_interpreters",
        "_multiprocessing",
        "_overlapped",
        "_queue",
        "_socket",
        "_wmi",
        "_zoneinfo",
        "select",
        "unicodedata",
        "winsound",
    ),
)


@dataclass(frozen=True)
class BundledLibrary:
    """A library python-build-standalone builds into the interpreter."""

    name: str
    expression: str
    # File name of its licence text in `LICENCES_DIR`.
    licence: str
    # The modules that contain or load it.
    modules: frozenset[str]
    # The platforms whose interpreter carries its own copy.
    platforms: frozenset[str] = ALL_PLATFORMS
    # Shared libraries of it next to the interpreter (Windows); None: always inside a module.
    files: re.Pattern[str] | None = None


@dataclass(frozen=True)
class LicenceOverride:
    """Licence data of a distribution that replaces what its metadata says."""

    expression: str
    # File names in `LICENCES_DIR`; empty: the distribution's own licence files.
    licences: tuple[str, ...] = ()


# Qt for Python's licence as this app uses it (the wheels offer LGPL-3.0 or the GPL).
QT_OVERRIDE = LicenceOverride("LGPL-3.0-only", ("qt.txt", "lgpl-3.0.txt", "gpl-3.0.txt"))
# Overrides by normalised distribution name (PEP 503).
LICENCE_OVERRIDES: Mapping[str, LicenceOverride] = MappingProxyType(
    {
        "pyside6-essentials": QT_OVERRIDE,
        "shiboken6": QT_OVERRIDE,
        "pyinstaller": LicenceOverride("GPL-2.0-or-later WITH Bootloader-exception"),
        "pyinstaller-hooks-contrib": LicenceOverride("GPL-2.0-or-later OR Apache-2.0"),
    },
)
# The platforms whose build of the interpreter carries its own copy of a library: Linux only.
LINUX_ONLY = frozenset(("linux",))
# The same: Linux and Windows.
LINUX_WINDOWS = frozenset(("linux", "win32"))
# The same: Linux and macOS.
LINUX_MACOS = frozenset(("linux", "darwin"))
# The libraries of python-build-standalone, by the module names that carry them.
BUNDLED_LIBRARIES = (
    BundledLibrary(
        "OpenSSL",
        "Apache-2.0",
        "apache-2.0.txt",
        frozenset(("_ssl", "_hashlib")),
        files=re.compile(r"lib(crypto|ssl)-\d+(-x64|-arm64)?\.dll", re.IGNORECASE),
    ),
    BundledLibrary(
        "SQLite",
        "blessing",
        "sqlite.txt",
        frozenset(("_sqlite3",)),
        files=re.compile(r"sqlite3\.dll", re.IGNORECASE),
    ),
    BundledLibrary(
        "libffi",
        "MIT",
        "libffi.txt",
        frozenset(("_ctypes",)),
        files=re.compile(r"libffi-\d+\.dll", re.IGNORECASE),
    ),
    BundledLibrary("XZ Utils (liblzma)", "0BSD", "xz.txt", frozenset(("_lzma",))),
    BundledLibrary("bzip2", "bzip2-1.0.6", "bzip2.txt", frozenset(("_bz2",))),
    BundledLibrary("mpdecimal", "BSD-2-Clause", "mpdecimal.txt", frozenset(("_decimal",))),
    BundledLibrary("Expat", "MIT", "expat.txt", frozenset(("pyexpat", "_elementtree"))),
    BundledLibrary("zlib", "Zlib", "zlib.txt", frozenset(("zlib",)), LINUX_WINDOWS),
    BundledLibrary("libuuid (util-linux)", "BSD-3-Clause", "libuuid.txt", frozenset(("_uuid",)), LINUX_MACOS),
    BundledLibrary("ncurses", "X11", "ncurses.txt", frozenset(("_curses", "_curses_panel")), LINUX_ONLY),
    BundledLibrary("libedit", "BSD-3-Clause", "libedit.txt", frozenset(("readline",)), LINUX_ONLY),
)
# The committed text of the licence of zlib (the notice of the macOS system copy ffmpeg links).
ZLIB_LICENCE = "zlib.txt"
# The notice of Microsoft's runtime libraries.
MICROSOFT_RUNTIME_LICENCE = "microsoft-runtime.txt"
