"""The pure parts of `scripts/build_desktop.py` (docs/ai/plans/m8.md, M8.1 tests)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from build_desktop import (
    HOOKS_DIR,
    PACKAGING_DIR,
    BuildPaths,
    Commit,
    asset_name,
    numeric_version,
    platform_tag,
    pyinstaller_arguments,
    release_tag,
    stamped_version,
    windows_version_resource,
)

# A commit hash.
SHA = "abc1234def5678abc1234def5678abc1234def56"
# Late on 28 November in UTC, already 29 November in Budapest (UTC+1).
LATE_UTC = datetime.fromisoformat("2026-11-28T23:30:00+00:00")
# The same moment as Budapest's local time.
BUDAPEST = timezone(timedelta(hours=1))


def _paths() -> BuildPaths:
    return BuildPaths(Path("/out/dist"), Path("/out/work/cli"))


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
    """The published names: `.exe` on Windows, the macOS GUI's disk image `.dmg` (A23), nothing else elsewhere."""
    tags = ("windows-x64", "linux-x64", "macos-arm64", "macos-x64")
    names = [asset_name(variant, tag) for tag in tags for variant in ("cli", "gui")]
    assert names == [
        "transcribe-ui-windows-x64-cli.exe",
        "transcribe-ui-windows-x64-gui.exe",
        "transcribe-ui-linux-x64-cli",
        "transcribe-ui-linux-x64-gui",
        "transcribe-ui-macos-arm64-cli",
        "transcribe-ui-macos-arm64-gui.dmg",
        "transcribe-ui-macos-x64-cli",
        "transcribe-ui-macos-x64-gui.dmg",
    ]


def test_cli_arguments_leave_qt_out() -> None:
    """The CLI: one-file console build without Qt or the GUI package, stamped, with the project hooks."""
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
    """The GUI is windowed with a version resource on Windows and a console-mode binary on Linux."""
    windows = pyinstaller_arguments("gui", "windows-x64", _paths())
    linux = pyinstaller_arguments("gui", "linux-x64", _paths())
    assert windows[-5:] == [
        "PySide6.QtTest",
        "--windowed",
        "--version-file",
        "/out/work/cli/version-resource.txt",
        str(PACKAGING_DIR / "entry_gui.py"),
    ]
    assert linux[-4:] == ["--exclude-module", "PySide6.QtTest", "--console", str(PACKAGING_DIR / "entry_gui.py")]
    assert "--version-file" not in linux
    assert "--onefile" in windows
    assert "--onefile" in linux


def test_macos_gui_is_a_onedir_app() -> None:
    """On macOS the GUI is a windowed onedir build named `Transcribe-UI` (the `.app`), written to the work folder.

    The disk image made from it goes to `dist`; PyInstaller's default icon stays until the project has one.
    """
    arguments = pyinstaller_arguments("gui", "macos-arm64", _paths())
    assert arguments[:8] == [
        "--noconfirm",
        "--clean",
        "--onedir",
        "--noupx",
        "--name",
        "Transcribe-UI",
        "--distpath",
        "/out/work/cli/app",
    ]
    assert arguments[-2:] == ["--windowed", str(PACKAGING_DIR / "entry_gui.py")]
    assert "--icon" not in arguments
    assert "--version-file" not in arguments


def test_macos_cli_stays_one_file() -> None:
    """The macOS CLI is still a one-file console binary in `dist`, named like the other platforms' files."""
    arguments = pyinstaller_arguments("cli", "macos-x64", _paths())
    assert arguments[2:8] == [
        "--onefile",
        "--noupx",
        "--name",
        "transcribe-ui-macos-x64-cli",
        "--distpath",
        "/out/dist",
    ]
    assert "--console" in arguments


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
