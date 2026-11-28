"""The macOS GUI's disk image and `.app` in the desktop smoke test (A23, plans/m8.md M8.4)."""

import plistlib
from contextlib import ExitStack
from pathlib import Path

import pytest
from smoke_desktop_bundle import REQUIRED_DATA, app_of, gui_problems, list_app, list_bundle
from smoke_desktop_checks import Smoke
from smoke_desktop_image import (
    ImageError,
    attach_command,
    check_signature,
    detach,
    detach_command,
    gui_executable,
    image_problems,
    is_disk_image,
    verify_command,
)
from smoke_desktop_process import Command, Environment, HostLauncher, RunResult

# The revisions of the fake app.
REVISIONS = ("__init__.py", "initial_schema.py")
# The main executable inside the app.
EXECUTABLE = "Contents/MacOS/Transcribe-UI"
# Where `run_process` is looked up by the module under test.
RUN_PROCESS = "smoke_desktop_image.run_process"
# Apple's disk image tool.
HDIUTIL_PATH = "/usr/bin/hdiutil"
# A mount point of the command tests.
MOUNT_POINT = Path("/t/volume")


def _write(path: Path, body: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def _app(root: Path) -> Path:
    """Write a GUI app laid out as PyInstaller writes it: data in `Resources`, binaries in `Frameworks`, links."""
    app = root / "Transcribe-UI.app"
    contents_dir = app / "Contents"
    _write(contents_dir / "Info.plist", plistlib.dumps({"CFBundleExecutable": "Transcribe-UI"}))
    _write(app / EXECUTABLE)
    revisions = (f"transcribe_ui/state/migrations/versions/{name}" for name in REVISIONS)
    names = (*REQUIRED_DATA, *revisions, "PySide6/Qt/translations/qtbase_hu.qm")
    for name in names:
        _write(contents_dir / "Resources" / name)
    for plugin in ("libqcocoa.dylib", "libqoffscreen.dylib"):
        _write(contents_dir / "Frameworks" / "PySide6" / "Qt" / "plugins" / "platforms" / plugin)
    _write(contents_dir / "Frameworks" / "pkg__dot__libs" / "libz.dylib")
    (contents_dir / "Frameworks" / "transcribe_ui").symlink_to("../Resources/transcribe_ui")
    (contents_dir / "Frameworks" / "pkg.libs").symlink_to("pkg__dot__libs")
    return app


class _Hdiutil:
    """Stands in for `run_process` of `hdiutil`: an attach mounts the app and its link (if `with_app`)."""

    def __init__(self, *, with_app: bool, attach_code: int = 0) -> None:
        self.with_app = with_app
        self.attach_code = attach_code
        self.commands: list[list[str]] = []
        self.mounted: Path | None = None

    def __call__(self, argv: Command, _env: Environment, _timeout_s: float, _stdin: bytes = b"") -> RunResult:
        command = list(argv)
        self.commands.append(command)
        if command[1] == "attach":
            self.mounted = Path(command[-1])
            if self.with_app:
                _app(self.mounted)
                (self.mounted / "Applications").symlink_to("/Applications")
            return RunResult(self.attach_code, b"", b"", 0)
        return RunResult(0, b"", b"", 0)


def _mounted_only(hdiutil: _Hdiutil) -> object:
    """Return `Path.is_mount` that says the fake mount point is mounted."""
    return lambda path: path == hdiutil.mounted


def test_app_of_the_main_executable() -> None:
    """The app is found from its main executable only; a one-file executable has none."""
    app = Path("/Volumes/x/Transcribe-UI.app")
    assert app_of(app / EXECUTABLE) == app
    assert app_of(Path("/dist/transcribe-ui-macos-arm64-cli")) is None
    assert app_of(Path("/x/Contents/MacOS/Transcribe-UI")) is None
    assert app_of(Path("Contents/MacOS/Transcribe-UI")) is None


def test_app_listing_passes_the_gui_rules(tmp_path: Path) -> None:
    """The app's files listed by their bundled names satisfy the GUI's rules; links and the executable are not listed."""
    app = _app(tmp_path)
    listing = list_bundle(str(app / EXECUTABLE))
    assert listing == list_app(app)
    assert not gui_problems(listing.entries, REVISIONS, "darwin")
    assert "pkg.libs/libz.dylib" in listing.entries
    assert not {"Transcribe-UI", "Info.plist", "pkg__dot__libs/libz.dylib"} & listing.entries


def test_hdiutil_commands() -> None:
    """Attached read-only at the given folder, hidden from Finder, nothing opened; detached, forced if asked."""
    assert attach_command(Path("/d/gui.dmg"), MOUNT_POINT) == [
        "/usr/bin/hdiutil",
        "attach",
        "/d/gui.dmg",
        "-readonly",
        "-nobrowse",
        "-noautoopen",
        "-mountpoint",
        "/t/volume",
    ]
    detach = ["/usr/bin/hdiutil", "detach", str(MOUNT_POINT)]
    assert detach_command(MOUNT_POINT, force=False) == detach
    assert detach_command(MOUNT_POINT, force=True) == [*detach, "-force"]


def test_image_problems(tmp_path: Path) -> None:
    """The image holds the app and the `Applications` link, nothing else; hidden files do not count."""
    good = tmp_path / "good"
    _app(good)
    (good / "Applications").symlink_to("/Applications")
    (good / ".fseventsd").mkdir()
    assert not image_problems(good)
    bad = tmp_path / "bad"
    _app(bad)
    (bad / "Applications").symlink_to("/Users")
    (bad / "README.txt").write_text("x", encoding="utf-8")
    assert image_problems(bad) == [
        "the image also holds README.txt",
        "Applications points at /Users, not /Applications",
    ]
    empty = tmp_path / "empty"
    empty.mkdir()
    assert image_problems(empty) == ["the image lacks Applications", "the image lacks Transcribe-UI.app"]


def test_an_executable_is_run_as_it_is(tmp_path: Path) -> None:
    """Only a disk image is attached; an executable is the GUI the checks run."""
    executable = tmp_path / "transcribe-ui-linux-x64-gui"
    assert not is_disk_image(executable)
    assert is_disk_image(tmp_path / "transcribe-ui-macos-x64-gui.dmg")
    with ExitStack() as stack:
        assert gui_executable(executable, stack) == executable


def test_the_image_is_detached_after_the_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The checks get the app's executable in the attached image, which is detached when the run ends."""
    hdiutil = _Hdiutil(with_app=True)
    monkeypatch.setattr(RUN_PROCESS, hdiutil)
    monkeypatch.setattr(Path, "is_mount", _mounted_only(hdiutil))
    with ExitStack() as stack:
        executable = gui_executable(tmp_path / "gui.dmg", stack)
        assert hdiutil.mounted is not None
        assert executable == hdiutil.mounted / "Transcribe-UI.app" / EXECUTABLE
        assert [command[1] for command in hdiutil.commands] == ["attach"]
    assert hdiutil.commands[-1] == detach_command(hdiutil.mounted, force=False)


def test_a_wrong_image_is_detached_too(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An image without the app fails the run and is still detached; a failed attach is reported."""
    hdiutil = _Hdiutil(with_app=False)
    monkeypatch.setattr(RUN_PROCESS, hdiutil)
    monkeypatch.setattr(Path, "is_mount", _mounted_only(hdiutil))
    with pytest.raises(ImageError, match=r"the image lacks Transcribe-UI\.app"), ExitStack() as stack:
        gui_executable(tmp_path / "gui.dmg", stack)
    assert [command[1] for command in hdiutil.commands] == ["attach", "detach"]
    failing = _Hdiutil(with_app=False, attach_code=1)
    monkeypatch.setattr(RUN_PROCESS, failing)
    with pytest.raises(ImageError, match="could not attach"), ExitStack() as stack:
        gui_executable(tmp_path / "gui.dmg", stack)


class _Codesign:
    """Stands in for `run_process` of `codesign`: answers with `code`."""

    def __init__(self, code: int) -> None:
        self.code = code
        self.commands: list[list[str]] = []

    def __call__(self, argv: Command, _env: Environment, _timeout_s: float, _stdin: bytes = b"") -> RunResult:
        self.commands.append(list(argv))
        return RunResult(self.code, b"", b"invalid signature", 0)


def _smoke(gui: Path) -> Smoke:
    return Smoke("cli", str(gui), "0.1.0", HostLauncher({}), 1, 1, hungarian_env={})


def _cannot_run(*_arguments: object) -> RunResult:
    raise FileNotFoundError(HDIUTIL_PATH)


def test_a_detach_that_cannot_run_is_a_warning(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When `hdiutil` cannot even be started, the detach warns and raises nothing."""
    monkeypatch.setattr(RUN_PROCESS, _cannot_run)
    monkeypatch.setattr(Path, "is_mount", lambda _path: True)
    detach(MOUNT_POINT)
    assert f"warning: {MOUNT_POINT} could not be detached" in capsys.readouterr().err


def test_signature_of_the_app_in_the_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """`gui-signature` verifies the app the GUI checks run, deep and strict; a rejected signature fails it."""
    app = Path("/t/volume/Transcribe-UI.app")
    assert verify_command(app) == ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)]
    accepted = _Codesign(0)
    monkeypatch.setattr(RUN_PROCESS, accepted)
    passed = check_signature(_smoke(app / EXECUTABLE)).passed
    assert (passed, accepted.commands) == (True, [verify_command(app)])
    monkeypatch.setattr(RUN_PROCESS, _Codesign(1))
    rejected = check_signature(_smoke(app / EXECUTABLE))
    assert not rejected.passed
    assert "codesign does not accept" in rejected.detail
    assert not check_signature(_smoke(Path("/dist/transcribe-ui-macos-arm64-gui"))).passed
