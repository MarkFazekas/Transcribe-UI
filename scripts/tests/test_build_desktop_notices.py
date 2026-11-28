"""The third-party notices over a fake list of collected files (docs/ai/plans/m8.md, M8.3 tests)."""

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from importlib.metadata import distribution
from pathlib import Path
from types import MappingProxyType

import pytest
from build_desktop_facts import describe_distribution, interpreter, notices_entry, write_config
from build_desktop_licences import BUNDLED_LIBRARIES, LICENCE_OVERRIDES, LICENCES_DIR, MICROSOFT_RUNTIME_LICENCE
from build_desktop_notices import BINARY, EXTENSION, CollectedEntry, NoticesError, file_key, generate
from build_desktop_notices_classes import BuildFacts, DistributionInfo, FfmpegBuild, Interpreter
from build_desktop_packages import RpmPackages, SystemPackage, debian_licences

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
# The answers of a fake rpm by the query's flag.
RPM_ANSWERS = MappingProxyType(
    {
        "-qf": "glib2\n",
        "-q": "2.68.4-16.el9\nLGPLv2+\n",
        "-qL": "/usr/share/licenses/glib2/COPYING\n",
    },
)
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


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=ENCODING)
    return path


def _distribution(layout: Layout, name: str, expression: str, *, licensed: bool = True) -> DistributionInfo:
    licences = (_write(layout.site / f"{name}.dist-info" / "LICENSE", f"{name} licence text"),) if licensed else ()
    return DistributionInfo(name, "1.0", expression, licences)


def _ffmpeg(layout: Layout, zlib: object) -> FfmpegBuild:
    _write(layout.ffmpeg / "licences" / "FFmpeg-COPYING.LGPLv2.1", "GNU LESSER GENERAL PUBLIC LICENSE 2.1")
    _write(layout.ffmpeg / "licences" / "zlib-LICENSE", "zlib licence of the source tree")
    configure = ["--disable-autodetect", "--enable-zlib", "--enable-pthreads"]
    return FfmpegBuild(
        layout.ffmpeg, MappingProxyType({"ffmpeg": dict(FFMPEG_SOURCE), "zlib": zlib, "configure": configure})
    )


def _facts(layout: Layout, *, platform: str = "linux", zlib: object = None) -> BuildFacts:
    """Return the facts of a build: alembic, PySide6 and shiboken6 (no licence files), the app, PyInstaller."""
    packages = {
        "alembic": _distribution(layout, "alembic", "MIT"),
        "PySide6/QtCore.abi3.so": _distribution(
            layout, "PySide6_Essentials", "LGPL-3.0-only OR GPL-2.0-only", licensed=False
        ),
        "shiboken6/Shiboken.abi3.so": _distribution(
            layout, "shiboken6", "LGPL-3.0-only OR GPL-2.0-only", licensed=False
        ),
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
    )


def _cli_files(layout: Layout) -> list[CollectedEntry]:
    """Return what a CLI build collects: the entry script, a stdlib module, alembic, ffmpeg and the stamp."""
    return [
        ("entry_cli", str(layout.packaging / "entry_cli.py"), "PYSOURCE"),
        ("json", str(layout.prefix / "lib" / "python3.13" / "json" / "__init__.py"), "PYMODULE"),
        ("alembic", str(layout.site / "alembic"), "PYMODULE"),
        ("transcribe_ui", str(layout.site / "transcribe_ui" / "__init__.py"), "PYMODULE"),
        ("a.namespace", "-", "PYMODULE"),
        ("libpython3.13.so.1.0", str(layout.prefix / "lib" / "libpython3.13.so.1.0"), BINARY),
        ("ffmpeg", str(layout.ffmpeg / "ffmpeg"), BINARY),
        ("ffprobe", str(layout.ffmpeg / "ffprobe"), BINARY),
        ("transcribe_ui/version.txt", str(layout.work / "version.txt"), "DATA"),
        ("QtCore", "Versions/A/QtCore", "SYMLINK"),
    ]


def _gui_files(layout: Layout) -> list[CollectedEntry]:
    """Return what a GUI build collects: the CLI's files plus PySide6 and shiboken6."""
    return [
        *_cli_files(layout),
        (QT_CORE, str(layout.site / QT_CORE), EXTENSION),
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
        )
    )


def test_gui_notices_hold_qt_under_the_lgpl(layout: Layout, facts: BuildFacts) -> None:
    """PySide6 and shiboken6 (no licence files of their own) get Qt's notice and the LGPL; a repeated text is referred to."""
    notices = generate(_gui_files(layout), facts)
    assert {"PySide6_Essentials 1.0", "shiboken6 1.0"} <= set(_entries(notices))
    assert "Licence: LGPL-3.0-only" in notices
    assert notices.count("GNU LESSER GENERAL PUBLIC LICENSE\n                       Version 3") == 1
    assert "(The same text as lgpl-3.0.txt of PySide6_Essentials above.)" in notices
    assert "https://download.qt.io/official_releases/QtForPython/" in notices


def test_a_distribution_without_a_licence_fails(
    layout: Layout, facts: BuildFacts, cli_files: list[CollectedEntry]
) -> None:
    """A collected distribution without a licence file or expression fails the build, naming it."""
    bare = DistributionInfo("bare", "2.0", "", ())
    distributions = {**facts.distributions, file_key(layout.site / "bare.py"): bare}
    collected = [*cli_files, ("bare", str(layout.site / "bare.py"), "PYMODULE")]
    problems = _problems(collected, replace(facts, distributions=MappingProxyType(distributions)))
    assert problems == ("bare: no licence expression", "bare: no licence file")


def test_a_file_from_nowhere_fails(tmp_path: Path, facts: BuildFacts, cli_files: list[CollectedEntry]) -> None:
    """A collected file that no distribution, the interpreter, the ffmpeg build or an OS package explains fails the build."""
    stray = tmp_path / "elsewhere" / "libstray.so.1"
    problems = _problems([*cli_files, ("libstray.so.1", str(stray), BINARY)], facts)
    assert len(problems) == 1
    assert problems[0].startswith(f"{stray}: no licence entry")


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
    names.update(("zlib.txt", MICROSOFT_RUNTIME_LICENCE))
    assert all((LICENCES_DIR / name).read_text(encoding=ENCODING).strip() for name in names)


def _rpm_answer(argv: Sequence[str]) -> str | None:
    """Answer an rpm query like the database of the `RPM_ANSWERS` package."""
    return RPM_ANSWERS.get(argv[1])


def test_rpm_packages_read_the_database() -> None:
    """The rpm database names the owner, the version, the licence and the licence files (`rpm -qL`)."""
    rpm = RpmPackages("rpm", _rpm_answer)
    assert rpm.owner(Path("/usr/lib64/libglib-2.0.so.0")) == GLIB
    expected = SystemPackage(GLIB, "2.68.4-16.el9", "LGPLv2+", (Path("/usr/share/licenses/glib2/COPYING"),))
    assert rpm.describe(GLIB) == expected


def test_debian_copyright_licence() -> None:
    """The licence of a Debian package is that of its `Files: *` paragraph, not of the packaging's."""
    text = "Format: x\n\nFiles: *\nCopyright: 1996 J\nLicense: BSD-variant\n text\n\nFiles: debian/*\nLicense: GPL-2\n"
    assert debian_licences(text) == "BSD-variant"
    assert not debian_licences("Free-form copyright text\n")


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
