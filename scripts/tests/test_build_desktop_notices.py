"""The third-party notices over a fake list of collected files (docs/ai/plans/m8.md, M8.3 tests)."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from importlib.metadata import distribution
from pathlib import Path
from types import MappingProxyType

import pytest
from build_desktop_distributions import describe_distribution, licence_expression
from build_desktop_facts import notices_entry, write_config
from build_desktop_interpreter import interpreter
from build_desktop_licences import (
    BUNDLED_LIBRARIES,
    LICENCE_OVERRIDES,
    LICENCES_DIR,
    MICROSOFT_RUNTIME_LICENCE,
    PYTHON_LICENCES,
    VENDORED_LIBRARIES,
)
from build_desktop_notices import BINARY, EXTENSION, CollectedEntry, NoticesError, file_key, generate
from build_desktop_notices_classes import BuildFacts, DistributionInfo, FfmpegBuild, Interpreter
from build_desktop_packages import DpkgPackages, QueryFailedError, RpmPackages, SystemPackage, debian_licences

# The kind of a collected Python module.
MODULE_KIND = "PYMODULE"
# Encoding of the files the tests write.
ENCODING = "utf-8"
# The pinned FFmpeg source as `ffmpeg-build.json` describes it.
FFMPEG_SOURCE = MappingProxyType(
    {
        "name": "FFmpeg",
        "version": "9.1",
        "urls": ["https://ffmpeg.org/releases/ffmpeg-9.1.tar.xz"],
        "sha256": "1a4d",
        "licence": "COPYING.LGPLv2.1",
        "served_from": "https://ffmpeg.org/releases/ffmpeg-9.1.tar.xz",
    },
)
# The pinned zlib source of a Linux or Windows build, downloaded from its fallback URL.
ZLIB_SOURCE = MappingProxyType(
    {
        "name": "zlib",
        "version": "1.4",
        "urls": [
            "https://github.com/madler/zlib/releases/download/v1.4/zlib-1.4.tar.gz",
            "https://zlib.net/fossils/zlib-1.4.tar.gz",
        ],
        "sha256": "02dd",
        "licence": "LICENSE",
        "served_from": "https://zlib.net/fossils/zlib-1.4.tar.gz",
    },
)
# The answers of a fake rpm by the query's flag (`{licence}` is a licence file's path).
RPM_ANSWERS = MappingProxyType(
    {
        "-qf": "glib2\n",
        "-q": "2.68.4-16.el9\nLGPLv2+\nglib2-2.68.4-16.el9.src.rpm\n",
        "-qL": "{licence}\n",
    },
)
# What dnf answers for the repository the package came from.
DNF_REPOSITORY = "baseos\n"
# The Qt version of the fake Qt wheels (the committed attributions' minor version).
QT_VERSION = "6.12.0"
# The ICU library the Linux PySide6 wheel vendors.
ICU = "PySide6/Qt/lib/libicuuc.so.73"
# A Qt platform plugin of the PySide6 wheel.
QT_PLUGIN = "PySide6/Qt/plugins/platforms/libqxcb.so"
# The OS package of GLib.
GLIB = "glib2"
# A Qt library of the PySide6 wheel.
QT_CORE = "PySide6/QtCore.abi3.so"


@dataclass(frozen=True)
class Layout:
    """The folders of a fake build machine."""

    root: Path

    @property
    def site(self) -> Path:
        """Return the build environment's site-packages."""
        return self.root / "venv" / "site-packages"

    @property
    def prefix(self) -> Path:
        """Return the interpreter's prefix."""
        return self.root / "python"

    @property
    def ffmpeg(self) -> Path:
        """Return the ffmpeg build."""
        return self.root / "ffmpeg"

    @property
    def work(self) -> Path:
        """Return the build's work folder."""
        return self.root / "work"

    @property
    def packaging(self) -> Path:
        """Return the app's packaging folder."""
        return self.root / "packaging"


@dataclass
class FakePackages:
    """An OS package database: the files each package installed and how each is described."""

    owners: dict[str, str]
    packages: dict[str, SystemPackage]

    def owner(self, path: Path) -> str | None:
        """Return the package that installed the file."""
        return self.owners.get(str(path))

    def describe(self, package: str) -> SystemPackage | None:
        """Return the package's description."""
        return self.packages.get(package)

    def last_failure(self) -> str:
        """Return why the last query failed: never in this fake."""
        return ""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=ENCODING)
    return path


def _distribution(
    layout: Layout, name: str, expression: str, *, licensed: bool = True, version: str = "1.0"
) -> DistributionInfo:
    licences = (_write(layout.site / f"{name}.dist-info" / "LICENSE", f"{name} licence text"),) if licensed else ()
    return DistributionInfo(name, version, expression, licences)


def _ffmpeg(layout: Layout, zlib: object) -> FfmpegBuild:
    _write(layout.ffmpeg / "licences" / "FFmpeg-COPYING.LGPLv2.1", "GNU LESSER GENERAL PUBLIC LICENSE 2.1")
    _write(layout.ffmpeg / "licences" / "zlib-LICENSE", "zlib licence of the source tree")
    configure = ["--disable-autodetect", "--enable-zlib", "--enable-pthreads"]
    return FfmpegBuild(
        layout.ffmpeg, MappingProxyType({"ffmpeg": dict(FFMPEG_SOURCE), "zlib": zlib, "configure": configure})
    )


def _facts(layout: Layout, *, platform: str = "linux", zlib: object = None) -> BuildFacts:
    """Return the facts of a build: alembic, PySide6 and shiboken6 (no licence files), the app, PyInstaller."""
    qt = "LGPL-3.0-only OR GPL-2.0-only"
    pyside = _distribution(layout, "PySide6_Essentials", qt, licensed=False, version=QT_VERSION)
    packages = {
        "alembic": _distribution(layout, "alembic", "MIT"),
        QT_CORE: pyside,
        QT_PLUGIN: pyside,
        ICU: pyside,
        "PySide6/Qt/lib/libstray.so.1": pyside,
        "shiboken6/Shiboken.abi3.so": _distribution(layout, "shiboken6", qt, licensed=False, version=QT_VERSION),
        "transcribe_ui/__init__.py": DistributionInfo("transcribe-ui", "0.1.0", "Apache-2.0", ()),
    }
    by_file = {file_key(layout.site / name): dist for name, dist in packages.items()}
    pyinstaller = _distribution(layout, "pyinstaller", "GPLv2-or-later with a special exception")
    licence = _write(layout.prefix / "lib" / "python3.13" / "LICENSE.txt", "PSF LICENSE AGREEMENT")
    builtins = frozenset(("_ssl", "_hashlib", "zlib", "_curses", "sys"))
    python = Interpreter(layout.prefix, "3.13.13", licence, builtins, MappingProxyType({"OpenSSL": "3.5.9"}))
    return BuildFacts(
        platform=platform,
        title="Transcribe-UI 0.1.0+20261128.abc1234 (transcribe-ui-linux-x64-cli)",
        own_folders=(layout.packaging, layout.work),
        distributions=MappingProxyType(by_file),
        pyinstaller=pyinstaller,
        interpreter=python,
        ffmpeg=_ffmpeg(layout, dict(ZLIB_SOURCE) if zlib is None else zlib),
        system=None,
        binary_folders=(layout.root / "venv", Path("/usr/lib64")),
    )


def _cli_files(layout: Layout) -> list[CollectedEntry]:
    """Return what a CLI build collects: the entry script, a stdlib module, alembic, ffmpeg and the stamp."""
    return [
        ("entry_cli", str(layout.packaging / "entry_cli.py"), "PYSOURCE"),
        ("json", str(layout.prefix / "lib" / "python3.13" / "json" / "__init__.py"), MODULE_KIND),
        ("alembic", str(layout.site / "alembic"), MODULE_KIND),
        ("transcribe_ui", str(layout.site / "transcribe_ui" / "__init__.py"), MODULE_KIND),
        ("a.namespace", "-", MODULE_KIND),
        ("libpython3.13.so.1.0", str(layout.prefix / "lib" / "libpython3.13.so.1.0"), BINARY),
        ("ffmpeg", str(layout.ffmpeg / "ffmpeg"), BINARY),
        ("ffprobe", str(layout.ffmpeg / "ffprobe"), BINARY),
        ("transcribe_ui/version.txt", str(layout.work / "version.txt"), "DATA"),
        ("QtCore", "Versions/A/QtCore", "SYMLINK"),
    ]


def _gui_files(layout: Layout) -> list[CollectedEntry]:
    """Return what a GUI build collects: the CLI's files plus PySide6 (a plugin, the vendored ICU) and shiboken6."""
    return [
        *_cli_files(layout),
        (QT_CORE, str(layout.site / QT_CORE), EXTENSION),
        (QT_PLUGIN, str(layout.site / QT_PLUGIN), BINARY),
        (ICU, str(layout.site / ICU), BINARY),
        ("shiboken6/Shiboken.abi3.so", str(layout.site / "shiboken6" / "Shiboken.abi3.so"), EXTENSION),
    ]


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    """Return the folders of a fake build machine."""
    return Layout(tmp_path)


@pytest.fixture
def facts(layout: Layout) -> BuildFacts:
    """Return the facts of a Linux build."""
    return _facts(layout)


@pytest.fixture
def cli_files(layout: Layout) -> list[CollectedEntry]:
    """Return what a CLI build collects."""
    return _cli_files(layout)


def _problems(collected: list[CollectedEntry], facts: BuildFacts) -> tuple[str, ...]:
    try:
        generate(collected, facts)
    except NoticesError as error:
        return error.problems
    msg = "the notices were generated although a file has no licence entry"
    raise AssertionError(msg)


def _entries(notices: str) -> list[str]:
    """Return the names and versions of the entries listed in the contents."""
    head = notices.split("\n\n=", 1)[0]
    return [line.removeprefix("  - ").rsplit(" (", 1)[0] for line in head.splitlines() if line.startswith("  - ")]


def test_cli_notices_hold_no_qt(facts: BuildFacts, cli_files: list[CollectedEntry]) -> None:
    """The CLI's notices list what it collected (alembic, Python and libpython's libraries, FFmpeg, zlib, PyInstaller), never Qt."""
    notices = generate(cli_files, facts)
    assert _entries(notices) == [
        "alembic 1.0",
        "FFmpeg 9.1",
        "ncurses bundled with Python 3.13.13",
        "OpenSSL 3.5.9",
        "pyinstaller 1.0",
        "Python 3.13.13",
        "zlib bundled with Python 3.13.13",
        "zlib (in ffmpeg and ffprobe) 1.4",
    ]
    assert "Qt" not in notices
    assert all(
        text in notices
        for text in (
            "Licence: GPL-2.0-or-later WITH Bootloader-exception",
            "PSF LICENSE AGREEMENT",
            "Source: https://ffmpeg.org/releases/ffmpeg-9.1.tar.xz (SHA-256 1a4d).",
            "This build downloaded it from https://zlib.net/fossils/zlib-1.4.tar.gz.",
            "./configure --disable-autodetect --enable-zlib --enable-pthreads",
            "published as an asset of the GitHub release that contains this executable",
            "mimalloc",
            "HACL*",
        )
    )


def test_gui_notices_hold_qt_under_the_lgpl(layout: Layout, facts: BuildFacts) -> None:
    """PySide6 and shiboken6 (no licence files of their own) get Qt's notice, the LGPL and Qt's attributions.

    The ICU the wheel vendors has its own entry; a repeated text is referred to.
    """
    notices = generate(_gui_files(layout), facts)
    assert {"PySide6_Essentials 6.12.0", "shiboken6 6.12.0", "ICU 73"} <= set(_entries(notices))
    assert "Licence: LGPL-3.0-only" in notices
    assert "HarfBuzz-NG" in notices
    assert notices.count("GNU LESSER GENERAL PUBLIC LICENSE\n                       Version 3") == 1
    assert all(
        text in notices
        for text in (
            "(The same text as lgpl-3.0.txt of PySide6_Essentials above.)",
            "https://download.qt.io/official_releases/QtForPython/",
        )
    )


def test_a_distribution_without_a_licence_fails(
    layout: Layout, facts: BuildFacts, cli_files: list[CollectedEntry]
) -> None:
    """A collected distribution without a licence file or expression fails the build, naming it."""
    bare = DistributionInfo("bare", "2.0", "", ())
    distributions = {**facts.distributions, file_key(layout.site / "bare.py"): bare}
    collected = [*cli_files, ("bare", str(layout.site / "bare.py"), MODULE_KIND)]
    problems = _problems(collected, replace(facts, distributions=MappingProxyType(distributions)))
    assert problems == ("bare: no licence expression", "bare: no licence file")


def test_a_file_from_nowhere_fails(tmp_path: Path, facts: BuildFacts, cli_files: list[CollectedEntry]) -> None:
    """A binary from a tool folder on PATH fails the build; so does one of the OS no package explains."""
    tool = tmp_path / "Program Files" / "Git" / "ucrt64" / "bin" / "libssl-3-x64.dll"
    unowned = Path("/usr/lib64/libstray.so.1")
    collected = [*cli_files, (tool.name, str(tool), BINARY), (unowned.name, str(unowned), BINARY)]
    problems = _problems(collected, facts)
    assert problems[0] == (
        f"{tool}: a binary from outside the build environment, the interpreter, the ffmpeg build and the OS's "
        "library folders (a tool folder on PATH?)"
    )
    assert problems[1].startswith(f"{unowned}: no licence entry")
    assert len(problems) == len(collected) - len(cli_files)


def test_an_unknown_extension_of_the_interpreter_fails(
    layout: Layout, facts: BuildFacts, cli_files: list[CollectedEntry]
) -> None:
    """An extension module of the interpreter that is neither CPython-only nor in the library table needs an entry."""
    tkinter = layout.prefix / "lib" / "python3.13" / "lib-dynload" / "_tkinter.cpython-313-x86_64-linux-gnu.so"
    problems = _problems([*cli_files, ("_tkinter", str(tkinter), EXTENSION)], facts)
    assert problems == (f"{tkinter}: an extension module of the interpreter without a licence entry",)


def test_windows_interpreter_files(layout: Layout, facts: BuildFacts) -> None:
    """On Windows the DLLs and modules next to the interpreter name their libraries; Microsoft's runtime has its notice."""
    interpreter = replace(facts.interpreter, builtin_modules=frozenset())
    windows = replace(_facts(layout, platform="win32"), interpreter=interpreter)
    files = ("libcrypto-3-x64.dll", "sqlite3.dll", "python313.dll", "VCRUNTIME140.dll", "ucrtbase.dll")
    modules = ("_sqlite3.pyd", "_uuid.pyd", "_socket.pyd", "_lzma.pyd")
    collected: list[CollectedEntry] = [(name, str(layout.prefix / name), BINARY) for name in files]
    collected.extend((name, str(layout.prefix / "DLLs" / name), EXTENSION) for name in modules)
    entries = _entries(generate(collected, windows))
    assert entries == [
        "Microsoft Visual C++ Runtime and Universal C Runtime as bundled",
        "OpenSSL 3.5.9",
        "pyinstaller 1.0",
        "Python 3.13.13",
        "SQLite bundled with Python 3.13.13",
        "XZ Utils (liblzma) bundled with Python 3.13.13",
    ]


def test_macos_ffmpeg_uses_the_system_zlib(layout: Layout, cli_files: list[CollectedEntry]) -> None:
    """On macOS ffmpeg links the OS's libz: its entry says so and quotes zlib's licence; libpython's built-ins link the OS's too."""
    facts = _facts(layout, platform="darwin", zlib="the system's libz (/usr/lib)")
    notices = generate(cli_files, facts)
    assert "zlib (in ffmpeg and ffprobe) the OS's" in _entries(notices)
    assert "ffmpeg and ffprobe use the system's libz (/usr/lib), which is part of the OS" in notices
    assert "ncurses" not in notices
    assert "Jean-loup Gailly" in notices


def test_os_packages_of_linux_libraries(tmp_path: Path, facts: BuildFacts, cli_files: list[CollectedEntry]) -> None:
    """A shared library of the build system is listed with its package; a package without licence files fails."""
    glib, dbus = Path("/usr/lib64/libglib-2.0.so.0"), Path("/usr/lib64/libdbus-1.so.3")
    licence = _write(tmp_path / "licenses" / GLIB / "COPYING", "GNU LGPL 2.1")
    system = FakePackages(
        {str(glib): GLIB, str(dbus): "dbus-libs"},
        {
            GLIB: SystemPackage(GLIB, "2.68.4-16.el9", "LGPL-2.1-or-later", (licence,)),
            "dbus-libs": SystemPackage("dbus-libs", "1.12.20-8.el9", "(GPL-2.0-or-later OR AFL-2.1)", ()),
        },
    )
    packaged = replace(facts, system=system)
    collected = [*cli_files, (glib.name, str(glib), BINARY)]
    notices = generate(collected, packaged)
    assert "glib2 2.68.4-16.el9" in _entries(notices)
    assert "Shared libraries of the build system's package glib2: libglib-2.0.so.0." in notices
    assert _problems([*collected, (dbus.name, str(dbus), BINARY)], packaged) == ("dbus-libs: no licence file",)


def test_every_committed_licence_exists() -> None:
    """Each licence text the tables name is committed and not empty."""
    names = {library.licence for library in BUNDLED_LIBRARIES}
    names.update(name for override in LICENCE_OVERRIDES.values() for name in override.licences)
    names.update(name for library in VENDORED_LIBRARIES for name in library.licences)
    names.update(("zlib.txt", MICROSOFT_RUNTIME_LICENCE, *PYTHON_LICENCES))
    assert all((LICENCES_DIR / name).read_text(encoding=ENCODING).strip() for name in names)


@dataclass(frozen=True)
class FakeRpm:
    """Answers rpm (and dnf) queries like the database of the `RPM_ANSWERS` package; fails like rpm otherwise."""

    licence: Path
    # What `rpm -qL` and `rpm -q` documents list (None: the `RPM_ANSWERS` licence).
    licences: str | None = None
    documents: str = "(contains no files)\n"

    def __call__(self, argv: Sequence[str]) -> str:
        """Answer one query."""
        if argv[0] == "dnf":
            return DNF_REPOSITORY
        answers = {**RPM_ANSWERS, "-qd": self.documents}
        if self.licences is not None:
            answers["-qL"] = self.licences
        answer = None if argv[-1] == "missing" else answers.get(argv[1])
        if answer is None:
            msg = f"`{' '.join(argv)}` exited 1: package {argv[-1]} is not installed"
            raise QueryFailedError(msg)
        return answer.format(licence=self.licence)


def test_rpm_packages_read_the_database(tmp_path: Path) -> None:
    """The rpm database names the owner, version, licence, licence files and source RPM; the vault URL comes from dnf's repository."""
    licence = _write(tmp_path / "COPYING", "GNU LGPL 2.1")
    rpm = RpmPackages("rpm", FakeRpm(licence), release="9.6", dnf="dnf")
    assert rpm.owner(Path("/usr/lib64/libglib-2.0.so.0")) == GLIB
    described = rpm.describe(GLIB)
    assert described is not None
    assert (described.version, described.expression) == ("2.68.4-16.el9", "LGPLv2+")
    assert described.licences == (licence,)
    vault = "https://vault.almalinux.org/9.6/BaseOS/Source/Packages/glib2-2.68.4-16.el9.src.rpm"
    assert described.source == f"Source: glib2-2.68.4-16.el9.src.rpm, {vault}"


def test_a_failed_package_query_says_why() -> None:
    """A query that fails keeps its exit status and error output for the problem text."""
    rpm = RpmPackages("rpm", FakeRpm(Path("/nowhere")))
    assert rpm.describe("missing") is None
    assert rpm.last_failure().endswith(" missing` exited 1: package missing is not installed")


def test_rpm_falls_back_to_licence_documents(tmp_path: Path) -> None:
    """A package without `%license` files: its licence-like documentation files count (only those that exist)."""
    notice = _write(tmp_path / "doc" / "COPYING", "MIT text")
    documents = f"{tmp_path / 'doc' / 'README'}\n{notice}\n{tmp_path / 'doc' / 'LICENSE.gone'}\n"
    rpm = RpmPackages("rpm", FakeRpm(notice, licences="(contains no files)\n", documents=documents))
    described = rpm.describe(GLIB)
    assert described is not None
    assert described.licences == (notice,)


def test_os_package_without_licence_files(facts: BuildFacts, cli_files: list[CollectedEntry]) -> None:
    """An OS package with no licence file gets the standard text of its licence; one the table cannot name fails.

    A library whose package states its source package's licence gets the library's own.
    """
    libx11, lz4, odd = Path("/usr/lib64/libX11.so.6"), Path("/usr/lib64/liblz4.so.1"), Path("/usr/lib64/libodd.so.1")
    system = FakePackages(
        {str(libx11): "libX11", str(lz4): "lz4-libs", str(odd): "odd"},
        {
            "libX11": SystemPackage("libX11", "1.7.0-9.el9", "MIT", ()),
            "lz4-libs": SystemPackage("lz4-libs", "1.9.3-5.el9", "GPLv2+ and BSD", ()),
            "odd": SystemPackage("odd", "1.0", "Odd License", ()),
        },
    )
    packaged = replace(facts, system=system)
    collected = [*cli_files, *((path.name, str(path), BINARY) for path in (libx11, lz4))]
    notices = generate(collected, packaged)
    assert all(
        text in notices
        for text in (
            "Licence: BSD-2-Clause",
            "The package states GPLv2+ and BSD; liblz4 is BSD-2-Clause; the GPL covers lz4's command-line tools.",
            "The package ships no licence file: the standard text of its licence follows.",
            "----- MIT.txt -----",
        )
    )
    assert _problems([*collected, (odd.name, str(odd), BINARY)], packaged) == ("odd: no licence file",)


@dataclass(frozen=True)
class FakeSearch:
    """Answers `dpkg-query --search` with the canned output of the searched path; fails for any other."""

    answers: Mapping[str, str]

    def __call__(self, argv: Sequence[str]) -> str:
        """Return the output for the path the query searches."""
        if argv[-1] not in self.answers:
            raise QueryFailedError(argv[-1])
        return self.answers[argv[-1]]


def test_dpkg_search_lines() -> None:
    """`dpkg-query --search` may name several packages, an architecture or a diversion: the first owner counts."""
    answers = {
        "/lib/a.so": "diversion by other from: /lib/a.so\nlibfoo1:amd64, libfoo-dev: /lib/a.so\n",
        "/lib/b.so": "libbar2: /lib/b.so\n",
    }

    dpkg = DpkgPackages("dpkg-query", FakeSearch(answers))
    assert (dpkg.owner(Path("/lib/a.so")), dpkg.owner(Path("/lib/b.so"))) == ("libfoo1", "libbar2")


def test_debian_copyright_licence() -> None:
    """The licence of a Debian package is that of its `Files: *` paragraph, not of the packaging's."""
    text = "Format: x\n\nFiles: *\nCopyright: 1996 J\nLicense: BSD-variant\n text\n\nFiles: debian/*\nLicense: GPL-2\n"
    assert debian_licences(text) == "BSD-variant"
    assert not debian_licences("Free-form copyright text\n")


def test_unknown_licence_metadata_is_missing() -> None:
    """`License: UNKNOWN` (old packaging tools) or an empty field falls back to the classifiers."""
    classifiers = ["License :: OSI Approved :: MIT License"]
    assert licence_expression({"License": "UNKNOWN"}, classifiers) == "MIT License"
    assert licence_expression({"License-Expression": " ", "License": ""}, classifiers) == "MIT License"


def test_built_in_modules_without_an_entry_fail(facts: BuildFacts, cli_files: list[CollectedEntry]) -> None:
    """A built-in module of libpython that is neither CPython's own nor in the table may carry a library: it fails."""
    builtins = facts.interpreter.builtin_modules | {"_gdbm", "_tkinter"}
    problems = _problems(cli_files, replace(facts, interpreter=replace(facts.interpreter, builtin_modules=builtins)))
    assert problems == (
        "the built-in module _gdbm of the interpreter has no licence entry",
        "the built-in module _tkinter of the interpreter has no licence entry",
    )


def test_qt_wheel_rules(layout: Layout, facts: BuildFacts) -> None:
    """A binary of a Qt wheel that is not Qt's own or vendored fails; so does a Qt other than the attributions'."""
    stray = layout.site / "PySide6/Qt/lib/libstray.so.1"
    problems = _problems([*_gui_files(layout), ("libstray.so.1", str(stray), BINARY)], facts)
    assert problems == (
        f"{stray}: a binary of PySide6_Essentials that is neither Qt's own nor a listed vendored library",
    )
    newer = replace(facts.distributions[file_key(layout.site / QT_CORE)], version="6.13.0")
    distributions = {**facts.distributions, file_key(layout.site / QT_CORE): newer}
    collected = [*_cli_files(layout), (QT_CORE, str(layout.site / QT_CORE), EXTENSION)]
    problems = _problems(collected, replace(facts, distributions=MappingProxyType(distributions)))
    expected = (
        "PySide6_Essentials 6.13.0: the attributions of Qt's third-party code are of Qt 6.12 "
        "(run scripts/build_qt_attributions.py)"
    )
    assert problems == (expected,)


@pytest.mark.parametrize(
    ("expression", "fails"),
    [
        ("GPL-3.0-or-later", True),
        ("LGPL-2.1+ and GPL-2+", True),
        ("(GPLv2+ or AFL) and GPLv2+", True),
        ("GPL-2.0-or-later OR Apache-2.0", False),
        ("LGPL-2.1-or-later", False),
        ("MIT AND (GPL-2.0-only OR BSD-3-Clause)", False),
    ],
)
def test_the_licence_policy(
    layout: Layout, facts: BuildFacts, cli_files: list[CollectedEntry], expression: str, *, fails: bool
) -> None:
    """The GPL or AGPL without an LGPL alternative fails the build; PyInstaller is exempt by name; a missing licence file fails."""
    dist = replace(_distribution(layout, "component", expression), missing_licences=("NOTICE",))
    distributions = {**facts.distributions, file_key(layout.site / "component.py"): dist}
    collected = [*cli_files, ("component", str(layout.site / "component.py"), MODULE_KIND)]
    problems = _problems(collected, replace(facts, distributions=MappingProxyType(distributions)))
    policy = f"component: its licence ({expression}) is the GPL without an LGPL alternative or a listed exemption"
    assert problems[0] == "component: the declared licence file NOTICE is missing"
    assert any(problem.startswith(policy) for problem in problems) is fails


def test_installed_distribution_and_interpreter_facts() -> None:
    """Real metadata: alembic's expression and licence file; the interpreter's licence and built-in modules."""
    alembic = describe_distribution(distribution("alembic"))
    assert alembic.expression == "MIT"
    assert [path.name for path in alembic.licences] == ["LICENSE"]
    assert alembic.licences[0].is_file()
    python = interpreter()
    assert python.licence is not None
    assert "sys" in python.builtin_modules


def test_notices_entry_writes_the_configured_file(
    layout: Layout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, facts: BuildFacts, cli_files: list[CollectedEntry]
) -> None:
    """The runner's call writes the notices where `write_config` said and names them as package data."""
    monkeypatch.setattr("build_desktop_facts.build_facts", lambda _config: facts)
    config = tmp_path / "notices.json"
    output = layout.work / "THIRD_PARTY_NOTICES.txt"
    layout.work.mkdir()
    write_config(config, output=output, title=facts.title, ffmpeg=layout.ffmpeg, own=facts.own_folders)
    assert json.loads(config.read_text(encoding=ENCODING))["own"] == [str(layout.packaging), str(layout.work)]
    entry = notices_entry(config, cli_files)
    assert entry == ("transcribe_ui/THIRD_PARTY_NOTICES.txt", str(output))
    assert output.read_text(encoding=ENCODING).startswith(f"THIRD-PARTY SOFTWARE NOTICES\n{facts.title}\n")
