"""The settings page's view-model: the requests it builds, per-field errors, refused saves (plan m6 M6.3).

The requests a scripted form state builds are golden files (`gui_settings_*`); each runs through
the parity runner's backend and CLI legs, so a GUI-built request is a valid CLI request with the
same response.
"""

import json
import subprocess
import sys
from collections.abc import Iterator
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from support.fakes import seeded_ports
from support.golden import assert_golden
from support.gui_settings import Heard, Recorded, Scripted, request_golden
from support.parity import run_one
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import ErrorBody, ErrorResponse, UpdateSettingsRequest
from transcribe_ui.cli.constants import EXIT_INVALID_REQUEST, EXIT_OK
from transcribe_ui.gui.settings.constants import FORM_FIELD
from transcribe_ui.gui.settings.draft import SettingsDraft, settings_document, update_request
from transcribe_ui.gui.settings.restart import changes_language
from transcribe_ui.gui.settings.texts import settings_hint
from transcribe_ui.gui.settings.viewmodel import PreferencesViewModel
from transcribe_ui.i18n.catalog import activate
from transcribe_ui.i18n.constants import HUNGARIAN
from transcribe_ui.state.settings import default_settings

VIDEO = "video_extensions"
SUBPROCESS_TIMEOUT_S = 60
# The settings area's Qt-free modules (its view-models and what they use).
QT_FREE_MODULES = (
    "about",
    "account_texts",
    "accounts",
    "cloud_setup",
    "constants",
    "draft",
    "hooks",
    "models",
    "problems",
    "restart",
    "startup",
    "status",
    "texts",
    "viewmodel",
)


@pytest.fixture
def gui_root(tmp_path: Path) -> Path:
    """Return the root of the view-model's backend (apart from the parity legs' roots)."""
    root = tmp_path / "gui"
    root.mkdir()
    return root


@pytest.fixture
def backend(gui_root: Path) -> Iterator[Backend]:
    """Return a seeded backend under `gui_root` (closed afterwards)."""
    with closing(Backend(seeded_ports(gui_root))) as created:
        yield created


def _loaded(runner: Backend | Scripted, heard: Heard) -> tuple[PreferencesViewModel, Recorded]:
    executor = Recorded(runner)
    model = PreferencesViewModel(executor, heard.hooks())
    model.load()
    return model, executor


def _draft(model: PreferencesViewModel) -> SettingsDraft:
    draft = model.shown.draft
    assert draft is not None
    return draft


def _edited(draft: SettingsDraft, root: Path) -> SettingsDraft:
    """The scripted form state of the update golden: every field changed."""
    languages = draft.with_language("en", enabled=False).with_language("de", enabled=True)
    return replace(
        languages,
        video_extensions="mkv mp4, webm",
        audio_extensions="flac;mp3",
        include_hidden=True,
        ffmpeg_path=f"  {root / 'tools' / 'ffmpeg'}  ",
        strategy="flat_with_folders",
        output_folder=str(root / "subtitles"),
        folder_count=2,
        overwrite=True,
        language=HUNGARIAN,
    )


def _sent_update(executor: Recorded) -> UpdateSettingsRequest:
    request = executor.sent[-1]
    assert isinstance(request, UpdateSettingsRequest)
    return request


def test_save_sends_the_whole_form(tmp_path: Path, gui_root: Path, backend: Backend) -> None:
    """Every field of the form reaches `update_settings`; the golden runs on the backend and CLI legs."""
    heard = Heard()
    model, executor = _loaded(backend, heard)
    assert (model.shown.state, model.shown.can_save) == ("ready", False)
    model.edit(_edited(_draft(model), gui_root))
    model.save()
    request = _sent_update(executor)
    settings = request.settings
    assert (settings.enabled_languages, settings.video_extensions) == ((HUNGARIAN, "de"), ("mkv", "mp4", "webm"))
    golden = request_golden(request, gui_root)
    assert_golden("gui_settings_update.json", golden)
    assert run_one(tmp_path, golden).exit_code == EXIT_OK
    # Saved: the form shows the stored settings; the window hears of the save and of the new language.
    shown = model.shown
    assert (shown.message, shown.can_save, shown.errors, shown.draft) == ("Saved.", False, {}, shown.saved)
    assert (heard.saves, heard.language_changes, heard.errors) == (1, 1, [])


def test_cleared_paths_are_sent_as_none(tmp_path: Path, gui_root: Path, backend: Backend) -> None:
    """Emptied path fields mean the bundled tools and no output folder; the language stays: no restart."""
    tools = {"ffmpeg_path": str(gui_root / "ffmpeg"), "ffprobe_path": str(gui_root / "ffprobe")}
    stored = default_settings().model_copy(update=tools)
    assert backend.execute(UpdateSettingsRequest(action="update_settings", settings=stored)).status == "ok"
    heard = Heard()
    model, executor = _loaded(backend, heard)
    model.edit(replace(_draft(model), ffmpeg_path="", ffprobe_path=" ", output_folder=""))
    model.save()
    request = _sent_update(executor)
    assert (request.settings.ffmpeg_path, request.settings.ffprobe_path) == (None, None)
    golden = request_golden(request, gui_root)
    assert_golden("gui_settings_tools_cleared.json", golden)
    assert run_one(tmp_path, golden).exit_code == EXIT_OK
    assert (heard.saves, heard.language_changes) == (1, 0)


def test_refused_fields_keep_the_edits(backend: Backend) -> None:
    """A refused form is not sent; its fields show how to fix them and keep what was typed."""
    model, executor = _loaded(backend, Heard())
    sent_before = len(executor.sent)
    typed = replace(_draft(model), video_extensions=".MKV mp4", audio_extensions="mp3 mp4")
    model.edit(typed)
    model.save()
    assert (len(executor.sent), model.shown.draft) == (sent_before, typed)
    assert model.shown.errors == {VIDEO: settings_hint(VIDEO)}
    # Editing the field clears its error; once the pattern fits, the overlap is a problem of the whole form.
    model.edit(replace(typed, video_extensions="mkv mp4"))
    assert model.shown.errors == {}
    model.save()
    assert model.shown.errors == {FORM_FIELD: settings_hint(FORM_FIELD)}


def test_refused_form_gets_the_backend_response(tmp_path: Path, backend: Backend) -> None:
    """The form's own `invalid_request` is exactly the backend's and the CLI's response to its document."""
    model, _executor = _loaded(backend, Heard())
    model.edit(replace(_draft(model), video_extensions="mkv mp4", audio_extensions="mp3 mp4"))
    document = json.dumps(settings_document(_draft(model)), indent=2)
    assert_golden("gui_settings_refused_overlap.json", f"{document}\n")
    response = update_request(_draft(model))
    assert isinstance(response, ErrorResponse)
    exchange = run_one(tmp_path, document)
    assert exchange.exit_code == EXIT_INVALID_REQUEST
    assert json.loads(exchange.response) == response.model_dump(mode="json")


def test_backend_refusal_keeps_the_edits(backend: Backend) -> None:
    """A save the backend refuses (`settings_unwritable`) shows the error and keeps the form as typed."""
    refused = ErrorResponse(
        action="update_settings",
        error=ErrorBody(code="settings_unwritable", message="The settings cannot be saved."),
    )
    heard = Heard()
    model, _executor = _loaded(Scripted(backend, MappingProxyType({"update_settings": refused})), heard)
    typed = replace(_draft(model), include_hidden=True, language=HUNGARIAN)
    model.edit(typed)
    model.save()
    shown = model.shown
    assert (shown.draft, shown.saving, shown.can_save, shown.errors) == (typed, False, True, {})
    assert ([view.code for view in heard.errors], heard.saves, heard.language_changes) == (
        ["settings_unwritable"],
        0,
        0,
    )
    model.revert()
    assert model.shown.draft == model.shown.saved


def test_unreadable_settings_can_be_restored(tmp_path: Path, gui_root: Path, backend: Backend) -> None:
    """A failed load leaves nothing to edit; Restore defaults saves the defaults and loads the form."""
    unreadable = ErrorResponse(
        action="get_settings",
        error=ErrorBody(code="settings_invalid", message="The settings file is not valid."),
    )
    model, executor = _loaded(Scripted(backend, MappingProxyType({"get_settings": unreadable})), Heard())
    assert (model.shown.state, model.shown.draft) == ("failed", None)
    assert model.shown.load_error.startswith("The settings file cannot be read")
    model.restore_defaults()
    golden = request_golden(_sent_update(executor), gui_root)
    assert_golden("gui_settings_restore_defaults.json", golden)
    assert run_one(tmp_path, golden).exit_code == EXIT_OK
    assert model.shown.state == "ready"


def test_restart_only_when_the_language_changes() -> None:
    """`system` follows the OS language (none here: English), so only Hungarian changes English texts."""
    assert (changes_language(HUNGARIAN), changes_language("en"), changes_language("system")) == (True, False, False)
    activate(HUNGARIAN)
    assert (changes_language("system"), changes_language(HUNGARIAN)) == (True, False)


def test_the_view_models_do_not_load_qt() -> None:
    """The settings area's view-models import no Qt (their tests run without a display)."""
    lines = ["import sys", *(f"import transcribe_ui.gui.settings.{name}" for name in QT_FREE_MODULES)]
    lines.append("assert not [name for name in sys.modules if name.startswith(('PySide6', 'shiboken6'))]")
    completed = subprocess.run(  # noqa: S603 -- the test interpreter on a fixed script
        [sys.executable, "-c", "\n".join(lines)],
        capture_output=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
