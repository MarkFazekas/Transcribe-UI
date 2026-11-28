"""The pure parts of `scripts/build_desktop.py` (docs/ai/plans/m8.md, M8.1 tests)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from build_desktop import (
    HOOKS_DIR,
    PACKAGING_DIR,
    WINDOWS_ICON,
    BuildPaths,
    Commit,
    asset_name,
    numeric_version,
    pyinstaller_arguments,
    release_tag,
    stamped_version,
    windows_version_resource,
)
from build_platform import platform_tag

# A commit hash.
SHA = "abc1234def5678abc1234def5678abc1234def56"
# Late on 28 November in UTC, already 29 November in Budapest (UTC+1).
LATE_UTC = datetime.fromisoformat("2026-11-28T23:30:00+00:00")
# The same moment as Budapest's local time.
BUDAPEST = timezone(timedelta(hours=1))


def _paths() -> BuildPaths:
    return BuildPaths(Path("/out/dist"), Path("/out/work/cli"), Path("/out/ffmpeg"))


def test_stamp_and_tag_use_the_utc_commit_date() -> None:
    """The date is the committer date in UTC, whatever zone it was recorded in; the hash is shortened."""
    commit = Commit(SHA, LATE_UTC.astimezone(BUDAPEST))
    assert stamped_version("0.1.0", commit) == "0.1.0+20261128.abc1234"
    assert release_tag(commit) == "desktop-20261128-abc1234"


@pytest.mark.parametrize(
    ("system", "machine", "tag"),
    [
        ("win32", "AMD64", "windows-x64"),
        ("darwin", "arm64", "macos-arm64"),
        ("darwin", "x86_64", "macos-x64"),
        ("linux", "x86_64", "linux-x64"),
    ],
)
def test_platform_tags_of_the_four_builds(system: str, machine: str, tag: str) -> None:
    """Each of the four release platforms has its tag."""
    assert platform_tag(system, machine) == tag


def test_unknown_platform_is_refused() -> None:
    """A platform the release does not build for is an error, not a made-up name."""
    with pytest.raises(ValueError, match="no release build"):
        platform_tag("freebsd14", "amd64")


def test_asset_names() -> None:
    """The published names, with `.exe` only on Windows."""
    names = [asset_name(variant, tag) for tag in ("windows-x64", "linux-x64") for variant in ("cli", "gui")]
    assert names == [
        "transcribe-ui-windows-x64-cli.exe",
        "transcribe-ui-windows-x64-gui.exe",
        "transcribe-ui-linux-x64-cli",
        "transcribe-ui-linux-x64-gui",
    ]


def test_cli_arguments_leave_qt_out() -> None:
    """The CLI: one-file console build without Qt or the GUI package, stamped, with the hooks and ffmpeg."""
    arguments = pyinstaller_arguments("cli", "linux-x64", _paths())
    assert arguments == [
        "--noconfirm",
        "--clean",
        "--onefile",
        "--noupx",
        "--name",
        "transcribe-ui-linux-x64-cli",
        "--distpath",
        "/out/dist",
        "--workpath",
        "/out/work/cli/build",
        "--specpath",
        "/out/work/cli",
        "--additional-hooks-dir",
        str(HOOKS_DIR),
        "--copy-metadata",
        "transcribe-ui",
        "--copy-metadata",
        "keyring",
        "--collect-submodules",
        "keyring.backends",
        "--add-data",
        "/out/work/cli/version.txt:transcribe_ui",
        "--add-binary",
        "/out/ffmpeg/ffmpeg:.",
        "--add-binary",
        "/out/ffmpeg/ffprobe:.",
        "--exclude-module",
        "PySide6",
        "--exclude-module",
        "shiboken6",
        "--exclude-module",
        "transcribe_ui.gui",
        "--console",
        str(PACKAGING_DIR / "entry_cli.py"),
    ]


def test_gui_arguments_per_platform() -> None:
    """The GUI is windowed with a version resource and the app icon on Windows and a console-mode binary elsewhere."""
    windows = pyinstaller_arguments("gui", "windows-x64", _paths())
    macos = pyinstaller_arguments("gui", "macos-arm64", _paths())
    assert windows[-7:] == [
        "PySide6.QtNetwork",
        "--windowed",
        "--version-file",
        "/out/work/cli/version-resource.txt",
        "--icon",
        str(WINDOWS_ICON),
        str(PACKAGING_DIR / "entry_gui.py"),
    ]
    assert macos[-6:] == [
        "--exclude-module",
        "PySide6.QtTest",
        "--exclude-module",
        "PySide6.QtNetwork",
        "--console",
        str(PACKAGING_DIR / "entry_gui.py"),
    ]
    assert not {"--version-file", "--icon"} & set(macos)
    assert WINDOWS_ICON.is_file()
    assert "/out/ffmpeg/ffprobe.exe:." in windows


def test_numeric_version_pads_the_release_segment() -> None:
    """A Windows file version has four numbers, taken from the release segment of the base version."""
    assert [numeric_version(base) for base in ("0.1.0", "1.2", "2.0.0rc1", "1.2.3.4.5")] == [
        (0, 1, 0, 0),
        (1, 2, 0, 0),
        (2, 0, 0, 0),
        (1, 2, 3, 4),
    ]


def test_windows_version_resource_describes_the_file() -> None:
    """The resource is a PyInstaller version file with the variant's description and both versions."""
    resource = windows_version_resource("cli", "0.1.0", "0.1.0+20261128.abc1234", "windows-x64")
    assert "filevers=(0, 1, 0, 0)" in resource
    assert "StringStruct('FileDescription', 'Transcribe-UI command line')" in resource
    assert "StringStruct('ProductVersion', '0.1.0+20261128.abc1234')" in resource
    assert "StringStruct('OriginalFilename', 'transcribe-ui-windows-x64-cli.exe')" in resource
