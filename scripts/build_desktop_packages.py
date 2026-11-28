"""The OS packages of shared libraries a Linux build collects from the build system (docs/ai/plans/m8.md, M8.3).

Used by `scripts/build_desktop_notices.py`. PyInstaller bundles the shared libraries Qt and
other extensions load that are not part of every Linux system (GLib, D-Bus, X11, ...); the
release build takes them from the manylinux container (AlmaLinux 9, rpm), a local build from
its own system (Debian and Ubuntu: dpkg). The package database names each library's package,
its version, its licence, its licence files and its source package (with rpm, the AlmaLinux
vault URL of the source RPM). A failed query keeps its exit status and error output, so the
build's problem says why. Standard library only.
"""

import itertools
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

# Runs a query command; returns its standard output (raises `QueryFailedError` when it failed).
type Query = Callable[[Sequence[str]], str]

# Seconds one package query may take.
QUERY_TIMEOUT_S = 60
# The database dpkg keeps; present on Debian and Ubuntu.
DPKG_STATUS = Path("/var/lib/dpkg/status")
# Where Debian packages keep their copyright file (`<folder>/<package>/copyright`).
DPKG_DOCS = Path("/usr/share/doc")
# What rpm prints for a package without licence (or documentation) files.
RPM_NO_FILES = "(contains no files)"
# Documentation files that hold a licence (a package without `%license` files may ship them as docs).
LICENCE_DOCUMENT = re.compile(r"(COPYING|LICEN[CS]E|NOTICE|COPYRIGHT)([._-].*)?", re.IGNORECASE)
# The first path part below the root of what merged `/usr` makes available at `/`.
USR = ("usr",)
# What `rpm -qf` prints for a package: its name.
RPM_NAME = r"%{NAME}\n"
# What `rpm -q` prints for a package: version and release, the licence, the source RPM.
RPM_FIELDS = r"%{VERSION}-%{RELEASE}\n%{LICENSE}\n%{SOURCERPM}\n"
# What `dpkg-query --show` prints for a package: version, then the source package and its version.
DPKG_FIELDS = "${Version}\n${source:Package} ${source:Version}\n"
# The OS release file (ID and VERSION_ID name the AlmaLinux release of the build container).
OS_RELEASE = Path("/etc/os-release")
# Where AlmaLinux keeps the source RPMs of each release (`<vault>/<release>/<repository>/Source/Packages/`).
ALMALINUX_VAULT = "https://vault.almalinux.org"
# The AlmaLinux repositories by dnf's repository ids, as the vault names their folders.
ALMALINUX_REPOSITORIES = MappingProxyType({"baseos": "BaseOS", "appstream": "AppStream", "crb": "CRB"})
# What `dnf repoquery` prints for an installed package: the repository it was installed from.
DNF_FROM_REPO = "%{from_repo}"
# The fields `dpkg-query --search` prints before a path; a diversion line names no owner.
DPKG_DIVERSION = "diversion by "
# A `License:` field of a machine-readable Debian copyright file.
DPKG_LICENCE_FIELD = re.compile(r"^License:\s*(\S.*)$", re.MULTILINE)
# The paragraph of a machine-readable Debian copyright file that covers every file.
DPKG_ALL_FILES = re.compile(r"^Files:\s*\*\s*$", re.MULTILINE)
# The expression of a Debian package whose copyright file names no licence in machine-readable form.
DPKG_FREE_FORM = "see the copyright file"


@dataclass(frozen=True)
class SystemPackage:
    """An OS package: version, licence, licence files, and where its source is."""

    name: str
    version: str
    expression: str
    licences: tuple[Path, ...]
    # Its source package and where to get it ("" when unknown).
    source: str = ""


class SystemPackages(Protocol):
    """The package database of the build system."""

    def owner(self, path: Path) -> str | None:
        """Return the package that installed the file, or None."""
        ...

    def describe(self, package: str) -> SystemPackage | None:
        """Return the package's version and licence, or None when it is not installed."""
        ...

    def last_failure(self) -> str:
        """Return why the last failed query failed ("" when none failed)."""
        ...


class QueryFailedError(Exception):
    """A package query failed: its exit status and error output, or why it could not run."""


def run_query(argv: Sequence[str]) -> str:
    """Run a package query; return its standard output.

    Raises:
        QueryFailedError: The query could not run, timed out or exited with an error.
    """
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=QUERY_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        message = f"{argv[0]}: {error}"
        raise QueryFailedError(message) from error
    if completed.returncode:
        command = " ".join(argv)
        message = f"`{command}` exited {completed.returncode}: {completed.stderr.strip()}"
        raise QueryFailedError(message)
    return completed.stdout


class Database:
    """A package database's query runner that keeps the last failure."""

    def __init__(self, query: Query) -> None:
        """Run queries through `query`."""
        self._query = query
        self._failure = ""

    def last_failure(self) -> str:
        """Return why the last failed query failed ("" when none failed)."""
        return self._failure

    def ask(self, argv: Sequence[str]) -> str | None:
        """Return the query's output, or None (remembering why) when it failed."""
        try:
            return self._query(argv)
        except QueryFailedError as error:
            self._failure = str(error)
            return None


def _unquoted(field: str) -> str:
    """Return an OS release field without its surrounding space and quotes."""
    return field.strip().strip('"')


def _first_line(output: str) -> str:
    """Return the first line of a query's output, stripped."""
    return output.splitlines()[0].strip()


def _padded_lines(output: str, count: int) -> list[str]:
    """Return the first `count` lines of a query's output, stripped ("" for each missing one)."""
    lines = output.splitlines()
    padded = [*lines, *itertools.repeat("", count)]
    return [line.strip() for line in padded[:count]]


def _is_usr(path: Path) -> bool:
    return path.parts[1:2] == USR


def _without_usr(path: Path) -> Path:
    return Path("/", *path.parts[2:])


def candidates(path: Path) -> list[Path]:
    """Return the names a package database may know the file by: as given, resolved, and without `/usr` (merged `/usr`)."""
    names = [path, path.resolve()]
    merged = [_without_usr(name) for name in names if _is_usr(name)]
    return list(dict.fromkeys([*names, *merged]))


def _listed_files(listing: str) -> tuple[Path, ...]:
    """Return the files an `rpm -qL` or `rpm -qd` listing names."""
    lines = (line.strip() for line in listing.splitlines())
    named = (line for line in lines if line and line != RPM_NO_FILES)
    return tuple(map(Path, named))


def os_release(path: Path = OS_RELEASE) -> dict[str, str]:
    """Return the fields of the OS release file (empty when it cannot be read)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    assignments = (line for line in text.splitlines() if "=" in line)
    pairs = (line.split("=", 1) for line in assignments)
    return {key.strip(): _unquoted(field) for key, field in pairs}


def vault_source(srpm: str, release: str, repository: str | None) -> str:
    """Return where the source RPM of an AlmaLinux package is: its vault URL, or the folders it is in."""
    folder = ALMALINUX_REPOSITORIES.get(repository or "")
    if folder is not None:
        packages = f"{ALMALINUX_VAULT}/{release}/{folder}/Source/Packages"
        return f"Source: {srpm}, {packages}/{srpm}"
    folders = ", ".join(ALMALINUX_REPOSITORIES.values())
    return f"Source: {srpm}, in {ALMALINUX_VAULT}/{release}/<{folders}>/Source/Packages/"


class RpmPackages(Database):
    """The rpm database (the manylinux container of the release build)."""

    def __init__(
        self,
        rpm: str,
        query: Query = run_query,
        release: str = "",
        dnf: str | None = None,
    ) -> None:
        """Ask `rpm` (and `dnf` for the repository a package came from) through `query`.

        `release` is the AlmaLinux release whose vault holds the sources ("": not AlmaLinux).
        """
        super().__init__(query)
        self._rpm = rpm
        self._release = release
        self._dnf = dnf

    def owner(self, path: Path) -> str | None:
        """Return the package that installed the file, or None."""
        for name in candidates(path):
            command = [self._rpm, "-qf", "--queryformat", RPM_NAME]
            found = self.ask([*command, str(name)])
            if found:
                return _first_line(found)
        return None

    def describe(self, package: str) -> SystemPackage | None:
        """Return the package's version, licence, licence files (`rpm -qL`) and source RPM, or None."""
        fields = self.ask([self._rpm, "-q", "--queryformat", RPM_FIELDS, package])
        listed = self.ask([self._rpm, "-qL", package])
        if fields is None or listed is None:
            return None
        version, expression, srpm = _padded_lines(fields, 3)
        paths = _listed_files(listed)
        if not paths:
            documents = self.ask([self._rpm, "-qd", package]) or ""
            listed_documents = _listed_files(documents)
            paths = tuple(path for path in listed_documents if LICENCE_DOCUMENT.fullmatch(path.name))
        present = tuple(path for path in paths if path.is_file())
        return SystemPackage(package, version, expression, present, self._source(package, srpm))

    def _source(self, package: str, srpm: str) -> str:
        if not srpm or not self._release:
            return f"Source RPM: {srpm}" if srpm else ""
        return vault_source(srpm, self._release, self._repository(package))

    def _repository(self, package: str) -> str | None:
        """Return the dnf repository id the package was installed from, or None."""
        if self._dnf is None:
            return None
        command = [self._dnf, "-q", "--disablerepo=*", "repoquery", "--installed", "--queryformat", DNF_FROM_REPO]
        found = self.ask([*command, package])
        return _first_line(found.strip()) if found and found.strip() else None


def dpkg_owner(search_output: str) -> str | None:
    """Return the first package `dpkg-query --search` names (several packages, diversions, an architecture)."""
    for line in search_output.splitlines():
        if line.startswith(DPKG_DIVERSION) or ": " not in line:
            continue
        packages = line.split(": ", 1)[0]
        first = packages.split(",", 1)[0].strip()
        return first.split(":", 1)[0]
    return None


class DpkgPackages(Database):
    """The dpkg database (a local build on Debian or Ubuntu)."""

    def __init__(self, dpkg_query: str, query: Query = run_query, docs: Path = DPKG_DOCS) -> None:
        """Ask the `dpkg-query` executable through `query`; copyright files are under `docs`."""
        super().__init__(query)
        self._dpkg_query = dpkg_query
        self._docs = docs

    def owner(self, path: Path) -> str | None:
        """Return the package that installed the file (without its architecture), or None."""
        for name in candidates(path):
            found = self.ask([self._dpkg_query, "--search", str(name)])
            owner = None if found is None else dpkg_owner(found)
            if owner:
                return owner
        return None

    def describe(self, package: str) -> SystemPackage | None:
        """Return the package's version, its copyright file with the licence it names, and its source package."""
        fields = self.ask([self._dpkg_query, "--show", "--showformat", DPKG_FIELDS, package])
        if fields is None:
            return None
        version, source = _padded_lines(fields, 2)
        copyright_file = self._docs / package / "copyright"
        expression = DPKG_FREE_FORM
        if copyright_file.is_file():
            text = copyright_file.read_text(encoding="utf-8", errors="replace")
            expression = debian_licences(text) or DPKG_FREE_FORM
        note = f"Source package: {source}" if source else ""
        return SystemPackage(package, version, expression, (copyright_file,), note)


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
    if rpm is None:
        return None
    release = os_release()
    almalinux_release = ""
    if release.get("ID") == "almalinux":
        almalinux_release = release.get("VERSION_ID", "")
    return RpmPackages(rpm, release=almalinux_release, dnf=shutil.which("dnf"))
