"""Bundle checks of the desktop smoke test: what a one-file executable must and must not contain.

Used by `scripts/smoke_desktop_build.py`. The archive is listed with PyInstaller's own archive
viewer (`--list --recursive --brief`), so the smoke test runs in the build environment, where
PyInstaller is installed. Data and binaries are listed as paths (`transcribe_ui/version.txt`,
`PySide6/Qt/plugins/platforms/libqxcb.so`), Python modules by their dotted names. Both
executables carry their third-party notices (`transcribe_ui/THIRD_PARTY_NOTICES.txt`); only the
GUI carries the window icon.
"""

import os
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType

from smoke_desktop_process import run_process

# The repository's Alembic revisions, which every executable must bundle as files.
type Revisions = Sequence[str]

# PyInstaller's archive viewer, run as a module of the build environment's interpreter.
ARCHIVE_VIEWER = (sys.executable, "-m", "PyInstaller.utils.cliutils.archive_viewer")
# Seconds the archive viewer may take.
LIST_TIMEOUT_S = 300
# Lines of the archive viewer that are headings, not entries.
HEADING = re.compile(r"(Options in|Contents of) '.*'.*:")
# Package data every executable needs at run time.
REQUIRED_DATA = (
    "transcribe_ui/locale/en/LC_MESSAGES/transcribe_ui.mo",
    "transcribe_ui/locale/hu/LC_MESSAGES/transcribe_ui.mo",
    "transcribe_ui/state/migrations/env.py",
    "transcribe_ui/state/migrations/script.py.mako",
    "transcribe_ui/version.txt",
    "transcribe_ui/THIRD_PARTY_NOTICES.txt",
)
# The window icon's smallest and largest image, which only the GUI bundles (`gui/icon.py`).
GUI_DATA = (
    "transcribe_ui/gui/icons/transcribe-ui-16.png",
    "transcribe_ui/gui/icons/transcribe-ui-256.png",
)
# The media tools every executable bundles at its root (`media/tools.py` `bundle_dir`), by `sys.platform`.
MEDIA_TOOLS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "win32": ("ffmpeg.exe", "ffprobe.exe"),
        "darwin": ("ffmpeg", "ffprobe"),
        "linux": ("ffmpeg", "ffprobe"),
    },
)
# Folder of the bundled Alembic revisions.
REVISIONS_FOLDER = "transcribe_ui/state/migrations/versions"
# Suffixes of catalog sources, which are never bundled.
CATALOG_SOURCES = (".po", ".pot")
# The Hungarian texts of Qt itself (standard buttons), which the GUI installs.
QT_HUNGARIAN = "qtbase_hu.qm"
# The C++ runtime and GCC support library, which the Linux build leaves to the host.
HOST_LIBRARIES = re.compile(r"(libstdc\+\+|libgcc_s)\.so(\..*)?")
# Names that show Qt in a bundle: the Python packages (modules and files), the libraries of Qt and
# PySide6 wherever they are put, and the app's GUI package (its modules and its data).
QT_NAMES = re.compile(
    r"(PySide6|shiboken6)([./].*)?"
    r"|(.*/)?(lib)?(Qt6|pyside6|shiboken6)\w*\.(abi3\.)?(so|dll|dylib)(\..*)?"
    r"|transcribe_ui[./]gui([./].*)?",
    re.IGNORECASE,
)
# Qt Network's bindings and the TLS plugins, which the GUI leaves out (their OpenSSL backend would
# bundle whatever libssl the build machine has); Qt's own Network library may stay, as other Qt
# plugins (VNC, TUIO) link it.
QT_NETWORK = re.compile(r"PySide6\.QtNetwork|PySide6/QtNetwork\..+|(.*/)?plugins/tls/.*")
# Folder of the Qt platform plugins.
PLATFORMS_FOLDER = "platforms"
# Platform plugins the GUI needs: offscreen everywhere and the native ones, by `sys.platform`.
PLATFORM_PLUGINS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "win32": ("qoffscreen", "qwindows"),
        "darwin": ("qoffscreen", "qcocoa"),
        "linux": ("qoffscreen", "qxcb", "qwayland"),
    },
)
# Options of the archive viewer: list every archive, nested ones included, names only.
LIST_OPTIONS = ("--list", "--recursive", "--brief")


@dataclass(frozen=True)
class Listing:
    """An executable's archive listing, or why it could not be listed."""

    entries: frozenset[str]
    error: str | None = None


def list_archive(executable: str) -> Listing:
    """Return every entry of the executable's archives (nested ones included)."""
    viewed = run_process([*ARCHIVE_VIEWER, *LIST_OPTIONS, executable], os.environ, LIST_TIMEOUT_S)
    if viewed.returncode != 0:
        return Listing(frozenset(), f"the archive viewer failed (exit {viewed.returncode})\n{viewed.text()}")
    # Entries use the separator of the OS that built them; compare them in POSIX form.
    text = viewed.stdout.decode("utf-8", errors="replace").replace("\\", "/")
    lines = [line.strip() for line in text.splitlines()]
    return Listing(frozenset(line for line in lines if _is_entry(line)))


def _is_entry(line: str) -> bool:
    return bool(line) and HEADING.fullmatch(line) is None


def platform_plugins(entries: Iterable[str]) -> frozenset[str]:
    """Return the names of the bundled Qt platform plugins (`qxcb`, `qwindows`, ...)."""
    names = set()
    for entry in entries:
        path = PurePosixPath(entry)
        if path.parent.name == PLATFORMS_FOLDER:
            stem = path.name.split(".")[0]
            names.add(stem.removeprefix("lib"))
    return frozenset(names)


def common_problems(entries: frozenset[str], revisions: Revisions, platform: str) -> list[str]:
    """Return what both executables lack or must not have (empty: nothing); both bundle ffmpeg and ffprobe."""
    bundled_revisions = [f"{REVISIONS_FOLDER}/{name}" for name in revisions]
    required = [*REQUIRED_DATA, *bundled_revisions, *MEDIA_TOOLS[platform]]
    problems = [f"missing {name}" for name in required if name not in entries]
    problems.extend(
        f"bundles the catalog source {entry}" for entry in sorted(entries) if entry.endswith(CATALOG_SOURCES)
    )
    if platform == "linux":
        bundled = sorted(entry for entry in entries if _is_host_library(entry))
        problems.extend(f"bundles the host library {entry}" for entry in bundled)
    return problems


def _is_host_library(entry: str) -> bool:
    return HOST_LIBRARIES.fullmatch(PurePosixPath(entry).name) is not None


def cli_problems(entries: frozenset[str], revisions: Revisions, platform: str) -> list[str]:
    """Return what the CLI executable lacks or must not have: it never contains Qt or the GUI."""
    problems = common_problems(entries, revisions, platform)
    with_qt = [entry for entry in sorted(entries) if QT_NAMES.fullmatch(entry)]
    problems.extend(f"bundles {entry}" for entry in with_qt)
    return problems


def gui_problems(entries: frozenset[str], revisions: Revisions, platform: str) -> list[str]:
    """Return what the GUI executable lacks (Qt's Hungarian texts, the platform plugins, the window icon) or must not have (Qt Network)."""
    problems = common_problems(entries, revisions, platform)
    problems.extend(f"missing {name}" for name in GUI_DATA if name not in entries)
    network = (entry for entry in sorted(entries) if QT_NETWORK.fullmatch(entry))
    problems.extend(f"bundles {entry}" for entry in network)
    if not any(PurePosixPath(entry).name == QT_HUNGARIAN for entry in entries):
        problems.append(f"missing {QT_HUNGARIAN}")
    present = platform_plugins(entries)
    problems.extend(
        f"missing the platform plugin {plugin}" for plugin in PLATFORM_PLUGINS[platform] if plugin not in present
    )
    return problems
