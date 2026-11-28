"""The macOS GUI as an `.app` in a disk image (A23, plans/m8.md M8.4): `Info.plist`, staging, `hdiutil`."""

import plistlib
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest
from build_desktop_app import (
    IMAGE_ATTEMPTS,
    INFO_PLIST_VARIABLE,
    create_image_command,
    info_plist,
    is_macos_app,
    make_disk_image,
    plist_problems,
    prepare_app,
)

# The base version of the tests.
BASE = "0.1.0"
# A stamped version of it, committed on 5 November 2026.
STAMPED = f"{BASE}+20261105.abc1234"


def _app(root: Path, entries: Mapping[str, object]) -> Path:
    """Write an app folder with the given `Info.plist` entries."""
    app = root / "Transcribe-UI.app"
    (app / "Contents").mkdir(parents=True)
    with (app / "Contents" / "Info.plist").open("wb") as stream:
        plistlib.dump(dict(entries), stream)
    return app


class _Hdiutil:
    """Stands in for `subprocess.run` of `hdiutil create`: fails the first `failures` times."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **_options: object) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        failed = len(self.commands) <= self.failures
        return subprocess.CompletedProcess(command, 1 if failed else 0, "", "hdiutil: create failed - Resource busy")


def test_only_the_macos_gui_is_an_app() -> None:
    """The GUI on either macOS build is the app; the CLI and the other platforms' GUI are not."""
    assert is_macos_app("gui", "macos-arm64")
    assert is_macos_app("gui", "macos-x64")
    assert not is_macos_app("cli", "macos-arm64")
    assert not is_macos_app("gui", "windows-x64")
    assert not is_macos_app("gui", "linux-x64")


def test_info_plist_values() -> None:
    """The identifier, the base version, the commit date as up to three integers, the floor and high resolution."""
    assert info_plist(BASE, STAMPED) == {
        "CFBundleIdentifier": "io.github.markfazekas.transcribe-ui",
        "CFBundleShortVersionString": BASE,
        "CFBundleVersion": "2026.11.5",
        "LSMinimumSystemVersion": "14.0",
        "NSHighResolutionCapable": True,
    }


def test_short_version_keeps_only_numbers() -> None:
    """A pre-release base version gives its release segment, the numbers Apple accepts."""
    assert info_plist("2.0.0rc1", "2.0.0rc1+20261231.def5678")["CFBundleShortVersionString"] == "2.0.0"


def test_a_version_without_stamp_is_refused(tmp_path: Path) -> None:
    """The app's build needs the stamped version; anything else stops the build before PyInstaller runs."""
    with pytest.raises(ValueError, match="not a stamped version"):
        info_plist(BASE, BASE)
    with pytest.raises(RuntimeError, match="not a stamped version"):
        prepare_app(BASE, BASE, tmp_path / "entries.plist")
    assert not (tmp_path / "entries.plist").exists()


def test_prepare_app_writes_the_plist_file(tmp_path: Path) -> None:
    """The entries go to a plist file that the PyInstaller runner finds through the environment."""
    plist_file = tmp_path / "entries.plist"
    environment = prepare_app(BASE, STAMPED, plist_file)
    assert environment == {INFO_PLIST_VARIABLE: str(plist_file)}
    with plist_file.open("rb") as stream:
        assert plistlib.load(stream) == info_plist(BASE, STAMPED)


def test_plist_problems_name_each_differing_entry(tmp_path: Path) -> None:
    """An entry PyInstaller did not write, or wrote differently, is a problem; extra entries are not."""
    expected = info_plist(BASE, STAMPED)
    kept = {key: wanted for key, wanted in expected.items() if key != "LSMinimumSystemVersion"}
    app = _app(tmp_path, {**kept, "CFBundleVersion": "0.0.0", "CFBundleExecutable": "Transcribe-UI"})
    assert plist_problems(app, expected) == [
        "Info.plist: CFBundleVersion is '0.0.0', expected '2026.11.5'",
        "Info.plist: LSMinimumSystemVersion is None, expected '14.0'",
    ]
    assert not plist_problems(_app(tmp_path / "good", {**expected, "CFBundleName": "Transcribe-UI"}), expected)


def test_create_image_command() -> None:
    """A read-only lzfse-compressed HFS+ image named after the app, replacing an older file."""
    assert create_image_command(Path("/w/image"), Path("/d/transcribe-ui-macos-arm64-gui.dmg")) == [
        "/usr/bin/hdiutil",
        "create",
        "-volname",
        "Transcribe-UI",
        "-srcfolder",
        "/w/image",
        "-fs",
        "HFS+",
        "-format",
        "ULFO",
        "-ov",
        "/d/transcribe-ui-macos-arm64-gui.dmg",
    ]


def test_disk_image_holds_app_and_link(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The app is moved next to an `Applications` link, and that folder is packed; a busy `hdiutil` is tried again."""
    hdiutil = _Hdiutil(failures=1)
    monkeypatch.setattr(subprocess, "run", hdiutil)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    app = _app(tmp_path / "app", {"CFBundleExecutable": "Transcribe-UI"})
    staging = tmp_path / "image"
    image = tmp_path / "dist" / "transcribe-ui-macos-arm64-gui.dmg"
    assert make_disk_image(app, staging, image) == image
    command = create_image_command(staging, image)
    assert hdiutil.commands == [command, command]
    assert sorted(path.name for path in staging.iterdir()) == ["Applications", "Transcribe-UI.app"]
    assert (staging / "Applications").readlink() == Path("/Applications")
    # Moved, not copied: the app is where PyInstaller wrote it no more.
    assert not app.exists()


def test_disk_image_fails_after_its_tries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When every try fails, the build fails with each try's error."""
    hdiutil = _Hdiutil(failures=3)
    monkeypatch.setattr(subprocess, "run", hdiutil)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    app = _app(tmp_path / "app", {})
    with pytest.raises(RuntimeError, match=r"try 1: exit 1: hdiutil: create failed - Resource busy") as raised:
        make_disk_image(app, tmp_path / "image", tmp_path / "dist" / "gui.dmg")
    assert "try 3:" in str(raised.value)
    assert len(hdiutil.commands) == IMAGE_ATTEMPTS
