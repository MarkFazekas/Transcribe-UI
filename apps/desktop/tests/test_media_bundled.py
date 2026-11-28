"""The check of a release build's bundled ffmpeg and ffprobe (the GUI smoke mode, plan m8 §1)."""

import sys
from pathlib import Path

import pytest

from support.scripted import Raises, ScriptedRunner, exits
from transcribe_ui.media.classes import Tool
from transcribe_ui.media.constants import FFMPEG, FFPROBE, VERSION_TIMEOUT_S
from transcribe_ui.media.invoke import bundled_tool_problems
from transcribe_ui.media.runner import ProcessTimeoutError
from transcribe_ui.media.tools import executable_name

# Mode of an executable test file.
EXECUTABLE_MODE = 0o755


def _bundle(folder: Path, *tools: Tool) -> Path:
    """Write a fake bundle holding the given tools."""
    folder.mkdir(parents=True, exist_ok=True)
    for tool in tools:
        (folder / executable_name(tool)).write_bytes(b"")
    return folder


def test_both_tools_answer_from_the_bundle(tmp_path: Path) -> None:
    """Each tool is run from the bundle with `-version` under the short timeout; both exit 0: no problem."""
    folder = _bundle(tmp_path / "bundle", FFMPEG, FFPROBE)
    runner = ScriptedRunner([exits(), exits()])
    assert not bundled_tool_problems(folder, runner)
    assert runner.calls == [
        [str(folder / executable_name(FFMPEG)), "-version"],
        [str(folder / executable_name(FFPROBE)), "-version"],
    ]
    assert runner.timeouts == [VERSION_TIMEOUT_S, VERSION_TIMEOUT_S]


def test_a_missing_tool_is_not_looked_for_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tool missing from the bundle is a problem even with a copy on `PATH`; the other still runs."""
    folder = _bundle(tmp_path / "bundle", FFPROBE)
    on_path = _bundle(tmp_path / "bin", FFMPEG)
    (on_path / executable_name(FFMPEG)).chmod(EXECUTABLE_MODE)
    monkeypatch.setenv("PATH", str(on_path))
    runner = ScriptedRunner([exits()])
    problems = bundled_tool_problems(folder, runner)
    assert len(problems) == 1
    assert problems[0].startswith("ffmpeg: ffmpeg was not found")
    assert runner.calls == [[str(folder / executable_name(FFPROBE)), "-version"]]


def test_a_failing_or_hanging_tool_is_a_problem(tmp_path: Path) -> None:
    """A non-zero exit (with the end of stderr) and a timeout are each reported."""
    folder = _bundle(tmp_path / "bundle", FFMPEG, FFPROBE)
    runner = ScriptedRunner([exits(1, stderr=b"dyld: Library not loaded"), Raises(ProcessTimeoutError())])
    problems = bundled_tool_problems(folder, runner)
    assert problems[0] == "ffmpeg -version exited 1: dyld: Library not loaded"
    assert problems[1].startswith("ffprobe: ffprobe did not finish (version) within 30 s")


@pytest.mark.skipif(sys.platform == "win32", reason="creating a symbolic link needs a privilege on Windows")
def test_a_tool_outside_the_bundle_is_a_problem(tmp_path: Path) -> None:
    """A bundled entry that is a link to a copy elsewhere resolves outside the bundle: reported."""
    folder = _bundle(tmp_path / "bundle", FFPROBE)
    elsewhere = _bundle(tmp_path / "elsewhere", FFMPEG)
    (folder / executable_name(FFMPEG)).symlink_to(elsewhere / executable_name(FFMPEG))
    problems = bundled_tool_problems(folder, ScriptedRunner([exits(), exits()]))
    assert problems == [f"ffmpeg resolved to {folder / executable_name(FFMPEG)}, outside the bundle {folder}"]
