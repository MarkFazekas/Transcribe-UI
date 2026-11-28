"""The data the third-party notices are made from and made into (docs/ai/plans/m8.md, M8.3).

Used by `scripts/build_desktop_notices.py` and its helper modules, and by
`scripts/build_desktop_facts.py`, which gathers the facts. Standard library only.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from build_desktop_licences import LICENCES_DIR
from build_desktop_packages import SystemPackages

# Encoding of the notices and of the licence files read.
ENCODING = "utf-8"


def normalised(name: str) -> str:
    """Return a distribution name as PEP 503 normalises it."""
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass(frozen=True)
class LicenceText:
    """One licence file: its name and its text."""

    title: str
    text: str


@dataclass(frozen=True)
class Notice:
    """One entry of the notices."""

    name: str
    version: str
    expression: str
    licences: tuple[LicenceText, ...]
    # Where it comes from, its source, which files it is (empty: nothing to add).
    note: str = ""


@dataclass(frozen=True)
class DistributionInfo:
    """An installed distribution as the notices need it."""

    name: str
    version: str
    # Its licence expression ("" when the metadata states none).
    expression: str
    licences: tuple[Path, ...]

    @property
    def key(self) -> str:
        """Return the normalised name (PEP 503)."""
        return normalised(self.name)


@dataclass(frozen=True)
class Interpreter:
    """The interpreter the executable bundles (the build's own)."""

    prefix: Path
    version: str
    # Python's licence file (None: not found, which fails the build).
    licence: Path | None
    builtin_modules: frozenset[str]
    # Versions of bundled libraries the interpreter reports, by `BundledLibrary.name`.
    library_versions: Mapping[str, str]


@dataclass(frozen=True)
class FfmpegBuild:
    """The ffmpeg build both executables bundle: its folder and its `ffmpeg-build.json`."""

    folder: Path
    description: Mapping[str, object]


@dataclass(frozen=True)
class BuildFacts:
    """What the notices of one executable are made from, besides its table of contents."""

    # `sys.platform` of the build.
    platform: str
    # First line of the notices: the app, its version and the executable.
    title: str
    # Folders of the app's own build files.
    own_folders: tuple[Path, ...]
    # Installed distributions by the key (`file_key`) of each file they installed.
    distributions: Mapping[str, DistributionInfo]
    # PyInstaller's distribution (None: not installed, which fails the build).
    pyinstaller: DistributionInfo | None
    interpreter: Interpreter
    ffmpeg: FfmpegBuild
    # The OS package database (Linux), or None.
    system: SystemPackages | None
    licence_dir: Path = LICENCES_DIR


@dataclass
class Attribution:
    """What the collected files turned out to contain."""

    distributions: dict[str, DistributionInfo] = field(default_factory=dict)
    libraries: set[str] = field(default_factory=set)
    python: bool = False
    ffmpeg: bool = False
    microsoft: bool = False
    # Bundled file names by OS package.
    packages: dict[str, list[str]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
