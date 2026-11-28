"""The OS packages of shared libraries a Linux build collects from the build system (docs/ai/plans/m8.md, M8.3).

Used by `scripts/build_desktop_notices.py`. PyInstaller bundles the shared libraries Qt and
other extensions load that are not part of every Linux system (GLib, D-Bus, X11, ...); the
release build takes them from the manylinux container (AlmaLinux 9, rpm), a local build from
its own system (Debian and Ubuntu: dpkg). The package database names each library's package,
its version, its licence and its licence files. Standard library only.
"""

import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# Runs a query command; returns its standard output, or None when it failed.
type Query = Callable[[Sequence[str]], str | None]

# Seconds one package query may take.
QUERY_TIMEOUT_S = 60
# The database dpkg keeps; present on Debian and Ubuntu.
DPKG_STATUS = Path("/var/lib/dpkg/status")
# Where Debian packages keep their copyright file (`<folder>/<package>/copyright`).
DPKG_DOCS = Path("/usr/share/doc")
# What rpm prints for a package without licence files.
RPM_NO_FILES = "(contains no files)"
# The first path part below the root of what merged `/usr` makes available at `/`.
USR = ("usr",)
# What `rpm -qf` prints for a package: its name.
RPM_NAME = r"%{NAME}\n"
# What `rpm -q` prints for a package: version and release, then the licence.
RPM_FIELDS = r"%{VERSION}-%{RELEASE}\n%{LICENSE}\n"
# A `License:` field of a machine-readable Debian copyright file.
DPKG_LICENCE_FIELD = re.compile(r"^License:\s*(\S.*)$", re.MULTILINE)
# The paragraph of a machine-readable Debian copyright file that covers every file.
DPKG_ALL_FILES = re.compile(r"^Files:\s*\*\s*$", re.MULTILINE)
# The expression of a Debian package whose copyright file names no licence in machine-readable form.
DPKG_FREE_FORM = "see the copyright file"


@dataclass(frozen=True)
class SystemPackage:
    """An OS package: version, licence and licence files."""

    name: str
    version: str
    expression: str
    licences: tuple[Path, ...]


class SystemPackages(Protocol):
    """The package database of the build system."""

    def owner(self, path: Path) -> str | None:
        """Return the package that installed the file, or None."""
        ...

    def describe(self, package: str) -> SystemPackage | None:
        """Return the package's version and licence, or None when it is not installed."""
        ...


def run_query(argv: Sequence[str]) -> str | None:
    """Run a package query; return its standard output, or None when it failed or timed out."""
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=QUERY_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _is_usr(path: Path) -> bool:
    return path.parts[1:2] == USR


def _without_usr(path: Path) -> Path:
    return Path("/", *path.parts[2:])


def candidates(path: Path) -> list[Path]:
    """Return the names a package database may know the file by: as given, resolved, and without `/usr` (merged `/usr`)."""
    names = [path, path.resolve()]
    merged = [_without_usr(name) for name in names if _is_usr(name)]
    return list(dict.fromkeys([*names, *merged]))


class RpmPackages:
    """The rpm database (the manylinux container of the release build)."""

    def __init__(self, rpm: str, query: Query = run_query) -> None:
        """Ask the `rpm` executable through `query`."""
        self._rpm = rpm
        self._query = query

    def owner(self, path: Path) -> str | None:
        """Return the package that installed the file, or None."""
        for name in candidates(path):
            command = [self._rpm, "-qf", "--queryformat", RPM_NAME, str(name)]
            found = self._query(command)
            if found:
                return found.splitlines()[0].strip()
        return None

    def describe(self, package: str) -> SystemPackage | None:
        """Return the package's version, licence and licence files (`rpm -qL`), or None."""
        fields = self._query([self._rpm, "-q", "--queryformat", RPM_FIELDS, package])
        listed = self._query([self._rpm, "-qL", package])
        if fields is None or listed is None:
            return None
        answers = [*fields.splitlines(), "", ""]
        version, expression = answers[:2]
        lines = map(str.strip, listed.splitlines())
        paths = [Path(line) for line in lines if line and line != RPM_NO_FILES]
        return SystemPackage(package, version.strip(), expression.strip(), tuple(paths))


class DpkgPackages:
    """The dpkg database (a local build on Debian or Ubuntu)."""

    def __init__(self, dpkg_query: str, query: Query = run_query, docs: Path = DPKG_DOCS) -> None:
        """Ask the `dpkg-query` executable through `query`; copyright files are under `docs`."""
        self._dpkg_query = dpkg_query
        self._query = query
        self._docs = docs

    def owner(self, path: Path) -> str | None:
        """Return the package that installed the file (without its architecture), or None."""
        for name in candidates(path):
            found = self._query([self._dpkg_query, "--search", str(name)])
            if found:
                return found.split(":", 1)[0].strip()
        return None

    def describe(self, package: str) -> SystemPackage | None:
        """Return the package's version and its copyright file, with the licences that file names."""
        version = self._query([self._dpkg_query, "--show", "--showformat", "${Version}", package])
        if version is None:
            return None
        copyright_file = self._docs / package / "copyright"
        expression = DPKG_FREE_FORM
        if copyright_file.is_file():
            text = copyright_file.read_text(encoding="utf-8", errors="replace")
            expression = debian_licences(text) or DPKG_FREE_FORM
        return SystemPackage(package, version.strip(), expression, (copyright_file,))


def debian_licences(copyright_text: str) -> str:
    """Return the licence of the `Files: *` paragraph of a machine-readable Debian copyright file ("" for none).

    The other paragraphs name the licences of parts the library may not contain (the Debian
    packaging, build tools), so only the paragraph of every file states the library's licence.
    """
    for paragraph in copyright_text.split("\n\n"):
        if DPKG_ALL_FILES.search(paragraph):
            match = DPKG_LICENCE_FIELD.search(paragraph)
            return "" if match is None else match.group(1).strip()
    return ""


def build_system_packages() -> SystemPackages | None:
    """Return the package database of this system (dpkg where it keeps its status, else rpm), or None."""
    dpkg_query = shutil.which("dpkg-query")
    if dpkg_query is not None and DPKG_STATUS.is_file():
        return DpkgPackages(dpkg_query)
    rpm = shutil.which("rpm")
    return None if rpm is None else RpmPackages(rpm)
