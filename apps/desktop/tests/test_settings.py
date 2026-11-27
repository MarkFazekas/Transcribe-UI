"""Flow tests of the settings requests (plan m5 M5.2): round trip, validation, the stored file.

`get_settings` and `update_settings` run through `Backend.execute` and through the CLI's `main`
with the same normalised responses (golden files).
"""

import json
from pathlib import Path

import pytest

from support.fakes import app_paths_under, seeded_ports
from support.golden import assert_golden
from support.parity import run_one, run_parity
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import ErrorResponse, GetSettingsRequest, Response, Settings, UpdateSettingsRequest
from transcribe_ui.cli.constants import EXIT_INVALID_REQUEST, EXIT_OK
from transcribe_ui.state.constants import SETTINGS_TEMP_PREFIX

GET_SETTINGS = '{"action": "get_settings"}'


def _updated_settings() -> dict[str, object]:
    return {
        "enabled_languages": ["en", "de", "hu"],
        "video_extensions": ["mkv", "mp4"],
        "audio_extensions": ["flac", "mp3"],
        "include_hidden": True,
        "ffmpeg_path": "<root>/tools/ffmpeg",
        "ffprobe_path": None,
        "placement": {"strategy": "flat_with_folders", "output_folder": "<root>/subtitles", "folder_count": 2},
    }


def _update(settings: object) -> str:
    return json.dumps({"action": "update_settings", "settings": settings})


def _update_request(settings: Settings) -> UpdateSettingsRequest:
    return UpdateSettingsRequest(action="update_settings", settings=settings)


def _error_code(response: Response) -> str:
    assert isinstance(response, ErrorResponse)
    return response.error.code


def test_settings_round_trip_matches_on_both_legs(tmp_path: Path) -> None:
    """Defaults first, then the update is answered and read back identically on both legs."""
    default, updated, read_back = run_parity(tmp_path, [GET_SETTINGS, _update(_updated_settings()), GET_SETTINGS])
    assert {default.exit_code, updated.exit_code, read_back.exit_code} == {EXIT_OK}
    assert_golden("get_settings_default.json", default.golden())
    assert_golden("update_settings.json", updated.golden())
    assert json.loads(read_back.response)["settings"] == json.loads(updated.response)["settings"]


@pytest.mark.parametrize(
    ("name", "settings"),
    [
        ("unknown_language", {"enabled_languages": ["hu", "xx"]}),
        ("duplicate_language", {"enabled_languages": ["hu", "en", "hu"]}),
        ("no_language", {"enabled_languages": []}),
        ("bad_extension", {"video_extensions": [".MKV"]}),
        ("extension_in_both_classes", {"video_extensions": ["mka"], "audio_extensions": ["mka"]}),
        ("empty_path", {"ffmpeg_path": ""}),
        ("unknown_field", {"theme": "dark"}),
    ],
)
def test_invalid_settings_are_refused_on_both_legs(tmp_path: Path, name: str, settings: object) -> None:
    """Settings that break a machine-independent rule are refused before anything is stored."""
    exchange = run_one(tmp_path, _update(settings))
    assert exchange.exit_code == EXIT_INVALID_REQUEST
    assert_golden(f"update_settings_{name}.json", exchange.golden())
    assert not app_paths_under(tmp_path / "cli").settings_file.exists()


def test_settings_file_is_replaced_atomically(tmp_path: Path) -> None:
    """A save leaves exactly the settings file (no temp file) holding the new settings."""
    ports = seeded_ports(tmp_path)
    backend = Backend(ports)
    settings = Settings(enabled_languages=("en",), include_hidden=True)
    backend.execute(_update_request(Settings()))
    backend.execute(_update_request(settings))
    settings_file = ports.state.paths.settings_file
    assert [entry.name for entry in settings_file.parent.iterdir()] == [settings_file.name]
    assert Settings.model_validate_json(settings_file.read_bytes()) == settings
    assert not any(entry.name.startswith(SETTINGS_TEMP_PREFIX) for entry in settings_file.parent.iterdir())


def test_broken_settings_file_is_reported(tmp_path: Path) -> None:
    """A broken settings file gives `settings_invalid`; saving settings replaces it."""
    ports = seeded_ports(tmp_path)
    backend = Backend(ports)
    settings_file = ports.state.paths.settings_file
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text('{"enabled_languages": ["xx"]}', encoding="utf-8")
    get_request = GetSettingsRequest(action="get_settings")
    assert _error_code(backend.execute(get_request)) == "settings_invalid"
    backend.execute(_update_request(Settings()))
    assert backend.execute(get_request).status == "ok"


def test_settings_need_no_database(tmp_path: Path) -> None:
    """Settings requests never open the database, so they never create the data dir."""
    ports = seeded_ports(tmp_path)
    backend = Backend(ports)
    backend.execute(GetSettingsRequest(action="get_settings"))
    backend.execute(_update_request(Settings()))
    assert not ports.state.paths.data_dir.exists()


def test_unwritable_settings_are_reported(tmp_path: Path) -> None:
    """Settings that cannot be written give `settings_unwritable` (here the config dir is a file)."""
    ports = seeded_ports(tmp_path)
    config_dir = ports.state.paths.config_dir
    config_dir.parent.mkdir(parents=True)
    config_dir.write_bytes(b"")
    response = Backend(ports).execute(_update_request(Settings()))
    assert _error_code(response) == "settings_unwritable"
