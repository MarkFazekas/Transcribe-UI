"""What the third-party notices know beyond package metadata (docs/ai/plans/m8.md, M8.3).

Used by `scripts/build_desktop_notices.py`. Standard library only.

- The libraries python-build-standalone links into the interpreter, by the modules that use
  them: on Linux and macOS they are inside libpython (built-in modules), on Windows in the
  module's own `.pyd` or a DLL next to it. A library is listed where that platform's build
  carries its own copy, not where it links the OS's (macOS: zlib, ncurses, libedit).
- The modules of CPython that contain no third-party library (built into libpython or an
  extension module of their own), so they need no licence entry beyond Python's (with the
  software CPython incorporates); any other built-in or extension module of the interpreter
  fails the build until it gets an entry here.
- Licence data a distribution's metadata lacks or states vaguely (PySide6 and Shiboken6 ship
  no licence file; PyInstaller's licence is a sentence), the libraries the Qt wheels vendor
  besides Qt (ICU on Linux, Mesa's llvmpipe on Windows) and which other binaries of those
  wheels are Qt's own.
- The licence policy: an expression with the GPL or AGPL and neither an LGPL alternative nor a
  listed exemption fails the build.

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
# Modules of CPython without any third-party library in them, built in or of their own (the
# software CPython itself incorporates, such as HACL* in the hash modules, is in Python's entry).
CPYTHON_MODULES = frozenset(
    (
        # Built into libpython on Linux (python-build-standalone 3.13), most on every platform.
        "_abc",
        "_ast",
        "_asyncio",
        "_bisect",
        "_blake2",
        "_codecs",
        "_codecs_cn",
        "_codecs_hk",
        "_codecs_iso2022",
        "_codecs_jp",
        "_codecs_kr",
        "_codecs_tw",
        "_collections",
        "_contextvars",
        "_csv",
        "_datetime",
        "_functools",
        "_heapq",
        "_imp",
        "_interpchannels",
        "_interpqueues",
        "_interpreters",
        "_io",
        "_json",
        "_locale",
        "_lsprof",
        "_md5",
        "_multibytecodec",
        "_multiprocessing",
        "_opcode",
        "_operator",
        "_pickle",
        "_posixshmem",
        "_posixsubprocess",
        "_queue",
        "_random",
        "_sha1",
        "_sha2",
        "_sha3",
        "_signal",
        "_socket",
        "_sre",
        "_stat",
        "_statistics",
        "_string",
        "_struct",
        "_suggestions",
        "_symtable",
        "_sysconfig",
        "_testinternalcapi",
        "_thread",
        "_tokenize",
        "_tracemalloc",
        "_typing",
        "_warnings",
        "_weakref",
        "_xxtestfuzz",
        "_zoneinfo",
        "array",
        "atexit",
        "binascii",
        "builtins",
        "cmath",
        "errno",
        "faulthandler",
        "fcntl",
        "gc",
        "grp",
        "itertools",
        "marshal",
        "math",
        "mmap",
        "posix",
        "pwd",
        "resource",
        "select",
        "sys",
        "syslog",
        "termios",
        "time",
        "unicodedata",
        "xxsubtype",
        # Windows only (built in or `.pyd`).
        "_overlapped",
        "_winapi",
        "_wmi",
        "msvcrt",
        "nt",
        "winreg",
        "winsound",
        # macOS only.
        "_scproxy",
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


# The attributions of the third-party code inside Qt (`scripts/build_qt_attributions.py`).
QT_ATTRIBUTIONS = "qt-third-party.txt"
# The Qt minor version `QT_ATTRIBUTIONS` was made from; another bundled Qt fails the build.
QT_ATTRIBUTIONS_VERSION = "6.12"
# Qt for Python's licence as this app uses it (the wheels offer LGPL-3.0 or the GPL).
QT_OVERRIDE = LicenceOverride("LGPL-3.0-only", ("qt.txt", "lgpl-3.0.txt", "gpl-3.0.txt", QT_ATTRIBUTIONS))
# The Qt wheels (normalised names), whose binaries must be Qt's own or a listed vendored library.
QT_WHEELS = frozenset(("pyside6-essentials", "shiboken6"))
# Qt's own binaries in the Qt wheels: Qt's libraries and frameworks, the bindings, Shiboken.
QT_OWN_BINARIES = re.compile(
    r"(lib)?(qt6?\w*|pyside6\w*|shiboken6?\w*)((\.abi3)?\.(so|dll|pyd|dylib)(\.[\d.]+)?|\.abi3\.[\d.]+\.dylib)?",
    re.IGNORECASE,
)
# A folder of Qt's plugins (any binary inside one is Qt's own).
QT_PLUGINS_FOLDER = "plugins"
# Python's licence plus the software CPython incorporates.
PYTHON_LICENCES = ("cpython-incorporated.txt",)
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


@dataclass(frozen=True)
class VendoredLibrary:
    """A library a wheel ships next to its own code, recognised by its file name."""

    name: str
    expression: str
    licences: tuple[str, ...]
    files: re.Pattern[str]
    # What the entry says about it.
    note: str


# Libraries the Qt wheels vendor besides Qt, checked before the wheel's own licence applies.
VENDORED_LIBRARIES = (
    VendoredLibrary(
        "ICU",
        "Unicode-DFS-2016 AND ICU",
        ("icu.txt",),
        re.compile(r"libicu(data|i18n|uc|io|tu)\.so\.\d+(\.[\d.]+)?"),
        "Shipped by the Linux build of Qt for Python (libicudata, libicui18n, libicuuc).",
    ),
    VendoredLibrary(
        "Mesa llvmpipe (opengl32sw.dll)",
        "MIT AND Apache-2.0 WITH LLVM-exception",
        ("mesa.txt",),
        re.compile(r"opengl32sw\.dll", re.IGNORECASE),
        "The software OpenGL renderer shipped by the Windows build of Qt for Python.",
    ),
)
# Licences that are a GPL (or AGPL) in an expression; an LGPL is not one.
GPL_LICENCE = re.compile(r"\bA?GPL", re.IGNORECASE)
# Separators of alternatives in an expression (SPDX `OR`, and the lower-case `or` of rpm and Debian).
ALTERNATIVES = re.compile(r"\s+or\s+", re.IGNORECASE)
# The exception of the GCC runtime libraries.
GCC_EXCEPTION = "GCC runtime library exception"
# Entries (by name) whose GPL licence comes with an exception that keeps its terms off the rest of
# the executable, and the exception.
POLICY_EXEMPTIONS: Mapping[str, str] = MappingProxyType(
    {
        "pyinstaller": "the bootloader exception allows any program to be bundled with PyInstaller's bootloader",
        "libgcc": GCC_EXCEPTION,
        "libstdc++": GCC_EXCEPTION,
        "libgomp": GCC_EXCEPTION,
        "libatomic": GCC_EXCEPTION,
        "libquadmath": GCC_EXCEPTION,
        "libgfortran": GCC_EXCEPTION,
    },
)
# Folder (in `LICENCES_DIR`) of the standard licence texts by SPDX identifier, for an OS package
# that ships no licence file of its own.
SPDX_FOLDER = "spdx"
# The LGPL 2.1 or later, by its SPDX identifier.
LESSER_GPL_LATER = "LGPL-2.1-or-later"
# The SPDX identifier of each licence name an rpm `%{LICENSE}` field may use (old Fedora names).
SPDX_NAMES: Mapping[str, str] = MappingProxyType(
    {
        "MIT": "MIT",
        "BSD": "BSD-3-Clause",
        "BSD-2-Clause": "BSD-2-Clause",
        "BSD-3-Clause": "BSD-3-Clause",
        "LGPLv2+": LESSER_GPL_LATER,
        "LGPLv2.1+": LESSER_GPL_LATER,
        LESSER_GPL_LATER: LESSER_GPL_LATER,
        "GPLv2+": "GPL-2.0-or-later",
        "GPL-2.0-or-later": "GPL-2.0-or-later",
        "GPLv2": "GPL-2.0-only",
        "GPL-2.0-only": "GPL-2.0-only",
        "zlib": "Zlib",
        "Zlib": "Zlib",
    },
)
# Separators of the licence names in an expression.
LICENCE_SEPARATORS = re.compile(r"\s+(?:and|or|with)\s+|[()]", re.IGNORECASE)


@dataclass(frozen=True)
class PackageLicence:
    """The licence of the library an OS package holds, where the package's field states its source package's."""

    expression: str
    reason: str


# rpm fills `%{LICENSE}` of a library subpackage with the licences of the whole source package,
# command-line tools included; these packages hold only the library, whose licence this is.
# util-linux's libraries (libblkid, libmount), whose source package also holds GPL tools.
UTIL_LINUX_LIBRARY = PackageLicence(
    LESSER_GPL_LATER,
    "util-linux's libblkid and libmount are LGPL-2.1-or-later; the GPL covers util-linux's tools",
)
PACKAGE_LICENCES: Mapping[str, PackageLicence] = MappingProxyType(
    {
        "lz4-libs": PackageLicence("BSD-2-Clause", "liblz4 is BSD-2-Clause; the GPL covers lz4's command-line tools"),
        "keyutils-libs": PackageLicence(
            LESSER_GPL_LATER,
            "libkeyutils is LGPL-2.1-or-later; the GPL covers keyutils' tools",
        ),
        "dbus-libs": PackageLicence(
            "AFL-2.1 OR GPL-2.0-or-later",
            "libdbus is under the AFL 2.1 or the GPL; the GPL-only parts are D-Bus tools",
        ),
        "libzstd": PackageLicence("BSD-3-Clause OR GPL-2.0-only", "zstd's library is dual-licensed (BSD or GPL-2.0)"),
        # Debian and Ubuntu (a local build): the `Files: *` licence of these copyright files is
        # their source package's.
        "libblkid1": UTIL_LINUX_LIBRARY,
        "libmount1": UTIL_LINUX_LIBRARY,
        "libkeyutils1": PackageLicence(
            LESSER_GPL_LATER,
            "libkeyutils is LGPL-2.1-or-later; the GPL covers keyutils' tools",
        ),
        "libcom-err2": PackageLicence("MIT", "e2fsprogs' com_err library is under an MIT-style licence"),
    },
)
# The committed text of the licence of zlib (the notice of the macOS system copy ffmpeg links).
ZLIB_LICENCE = "zlib.txt"
# The notice of Microsoft's runtime libraries.
MICROSOFT_RUNTIME_LICENCE = "microsoft-runtime.txt"
