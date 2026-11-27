"""Finding ffmpeg and ffprobe: override, bundle, PATH (architecture §3.4)."""

import os
import sys
from functools import partial
from pathlib import Path

import pytest

from support.scripted import ScriptedRunner, error_of, which
from transcribe_ui.api.ports import CancelFlag
from transcribe_ui.media.adapter import FfmpegMediaTools
from transcribe_ui.media.classes import Tool
from transcribe_ui.media.constants import ERROR_MEDIA_TOOL_NOT_FOUND, FFMPEG, FFPROBE
from transcribe_ui.media.tools import ToolLocator, ToolOverrides, bundle_dir, executable_name, search_path

# Folder name of the fake bundle.
BUNDLE = "bundle"
# Mode of an executable test file.
EXECUTABLE_MODE = 0o755


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """Return a (fake) media file."""
    path = tmp_path / "film.mkv"
    path.write_bytes(b"film")
    return path


def _make_tool(folder: Path, tool: Tool) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / executable_name(tool)
    path.write_bytes(b"")
    return path


def test_override_comes_first(tmp_path: Path) -> None:
    """The settings path wins over the bundle and PATH, per tool."""
    override = _make_tool(tmp_path / "custom", FFMPEG)
    _make_tool(tmp_path / BUNDLE, FFMPEG)
    locator = ToolLocator(ToolOverrides(ffmpeg=override), bundle_dir=tmp_path / BUNDLE, which=which)

    assert (locator.resolve(FFMPEG), locator.resolve(FFPROBE)) == (override, Path(which(FFPROBE)))


def test_missing_override_is_an_error(tmp_path: Path) -> None:
    """A wrong settings path is reported, never silently replaced by another ffprobe."""
    _make_tool(tmp_path / BUNDLE, FFPROBE)
    locator = ToolLocator(ToolOverrides(ffprobe=tmp_path / "nowhere"), bundle_dir=tmp_path / BUNDLE, which=which)

    error = error_of(partial(locator.resolve, FFPROBE))

    assert (error.code, error.details["source"]) == (ERROR_MEDIA_TOOL_NOT_FOUND, "settings")


def test_bundle_comes_before_path(tmp_path: Path) -> None:
    """The bundled build is used even when PATH has another one; a tool it lacks comes from PATH."""
    bundled = _make_tool(tmp_path / BUNDLE, FFMPEG)
    locator = ToolLocator(bundle_dir=tmp_path / BUNDLE, which=which)

    assert (locator.resolve(FFMPEG), locator.resolve(FFPROBE)) == (bundled, Path(which(FFPROBE)))


def test_nothing_found(tmp_path: Path) -> None:
    """Without override, bundle or PATH entry the error says where to set it."""
    locator = ToolLocator(bundle_dir=tmp_path, which=lambda _tool: None)

    error = error_of(partial(locator.resolve, FFMPEG))

    assert (error.code, error.details["source"]) == (ERROR_MEDIA_TOOL_NOT_FOUND, "search")
    assert "settings" in error.message


def test_tool_not_found_runs_nothing(source: Path) -> None:
    """The runner is not called when the tool cannot be resolved."""
    runner = ScriptedRunner([])
    tools = FfmpegMediaTools(locator=ToolLocator(which=lambda _tool: None), runner=runner)

    error_of(partial(tools.extract, source, 0, CancelFlag()))

    assert not runner.calls


def test_bundle_dir_only_when_frozen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A PyInstaller build's unpack folder is the bundle; a normal run has none."""
    assert bundle_dir() is None
    monkeypatch.setattr(sys, "frozen", value=True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert bundle_dir() == tmp_path


def test_path_search_skips_relative_folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tool in the working folder or under a relative PATH entry is never found."""
    planted = tmp_path / "work"
    planted.mkdir()
    (planted / FFMPEG).write_bytes(b"")
    (planted / FFMPEG).chmod(EXECUTABLE_MODE)
    monkeypatch.chdir(planted)
    environ = {"PATH": os.pathsep.join([".", "work", ""])}

    assert search_path(FFMPEG, environ, windows=False) is None
    assert search_path(FFMPEG, {"PATH": os.pathsep.join(["", str(planted)])}, windows=False) == str(planted / FFMPEG)


def test_path_search_windows_extensions(tmp_path: Path) -> None:
    """On Windows each PATHEXT extension is tried, in order, in each absolute folder."""
    (tmp_path / "ffmpeg.bat").write_bytes(b"")
    (tmp_path / "ffmpeg.exe").write_bytes(b"")
    environ = {"PATH": str(tmp_path), "PATHEXT": ".EXE;.BAT"}

    assert search_path(FFMPEG, environ, windows=True) == str(tmp_path / "ffmpeg.exe")
    assert search_path(FFMPEG, {"PATH": str(tmp_path)}, windows=True) == str(tmp_path / "ffmpeg.exe")


def test_path_search_needs_an_executable_on_posix(tmp_path: Path) -> None:
    """A file without the execute bit is not a tool on POSIX."""
    if sys.platform == "win32":
        pytest.skip("Windows has no execute bit")
    (tmp_path / FFMPEG).write_bytes(b"")

    assert search_path(FFMPEG, {"PATH": str(tmp_path)}, windows=False) is None
