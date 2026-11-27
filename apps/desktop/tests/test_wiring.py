"""Flow tests of the composition root: the settings' ffmpeg/ffprobe override is read lazily."""

import json
import logging
from pathlib import Path

import pytest

from support.fakes import app_paths_under
from transcribe_ui.api.classes import GetAppInfoRequest
from transcribe_ui.api.errors import DomainError
from transcribe_ui.api.ports import CancelFlag
from transcribe_ui.api.wiring import build_backend, media_tools
from transcribe_ui.media.constants import FFMPEG, FFPROBE
from transcribe_ui.media.tools import ToolLocator, ToolOverrides
from transcribe_ui.state.settings import SettingsStore, default_settings


def _settings(tmp_path: Path) -> SettingsStore:
    return SettingsStore(app_paths_under(tmp_path).settings_file)


def _write_settings(tmp_path: Path, text: str) -> None:
    settings_file = app_paths_under(tmp_path).settings_file
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(text, encoding="utf-8")


def test_broken_settings_fail_only_tool_calls(tmp_path: Path) -> None:
    """A broken settings file does not stop the backend; a tool call gives `settings_invalid`."""
    _write_settings(tmp_path, "{not json")
    backend = build_backend(app_paths_under(tmp_path))
    assert backend.execute(GetAppInfoRequest(action="get_app_info")).status == "ok"
    with pytest.raises(DomainError) as refused:
        media_tools(_settings(tmp_path)).probe(tmp_path / "clip.mkv", CancelFlag())
    assert refused.value.code == "settings_invalid"


def test_override_from_settings_is_used_at_lookup(tmp_path: Path) -> None:
    """The path set in the settings is used, and a change applies to the next lookup."""
    tool = tmp_path / "custom-ffprobe"
    tool.write_bytes(b"")
    settings = _settings(tmp_path)
    locator = media_tools(settings).locator
    settings.save(default_settings().model_copy(update={"ffprobe_path": str(tool)}))
    assert locator.resolve(FFPROBE) == tool
    settings.save(default_settings().model_copy(update={"ffmpeg_path": str(tmp_path / "missing")}))
    with pytest.raises(DomainError) as missing:
        locator.resolve(FFMPEG)
    assert (missing.value.code, missing.value.details["source"]) == ("media_tool_not_found", "settings")


def test_unrelated_bad_field_keeps_tools_working(tmp_path: Path) -> None:
    """Only the two path fields matter to the tool lookup; a bad language elsewhere does not."""
    tool = tmp_path / "custom-ffmpeg"
    tool.write_bytes(b"")
    _write_settings(tmp_path, json.dumps({"enabled_languages": ["xx"], "ffmpeg_path": str(tool)}))
    assert media_tools(_settings(tmp_path)).locator.resolve(FFMPEG) == tool


def test_broken_path_field_fails_and_warns_once(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A broken path field gives `settings_invalid` at every lookup, but one warning per process."""
    _write_settings(tmp_path, json.dumps({"ffmpeg_path": "relative/ffmpeg"}))
    locator = media_tools(_settings(tmp_path)).locator
    for _ in range(2):
        with pytest.raises(DomainError) as refused:
            locator.resolve(FFMPEG)
        assert refused.value.code == "settings_invalid"
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1


def test_relative_override_is_refused_at_lookup() -> None:
    """A relative override is never run through a PATH lookup: it is `media_tool_not_found`."""
    locator = ToolLocator(overrides=ToolOverrides(ffmpeg=Path("ffmpeg")))
    with pytest.raises(DomainError) as refused:
        locator.resolve(FFMPEG)
    assert (refused.value.code, refused.value.details["source"]) == ("media_tool_not_found", "settings")
