"""The third-party licence notices: `get_third_party_notices` and the About section's dialog (plan m8 M8.3).

A release build bundles `THIRD_PARTY_NOTICES.txt` as package data; from source there is none.
The bundle is faked the way `test_app_version.py` fakes the version stamp.
"""

from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QPlainTextEdit
from pytestqt.qtbot import QtBot

from support.fakes import seeded_ports
from support.golden import assert_golden
from support.gui_settings import Deferred, Recorded, Scripted
from support.parity import run_one
from transcribe_ui.api import info
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import (
    ErrorBody,
    ErrorResponse,
    GetAppInfoResponse,
    GetThirdPartyNoticesResponse,
    Response,
)
from transcribe_ui.api.constants import ERROR_INTERNAL
from transcribe_ui.api.schema import response_adapter
from transcribe_ui.cli.constants import EXIT_OK
from transcribe_ui.constants import THIRD_PARTY_NOTICES_FILE
from transcribe_ui.gui.feedback import ErrorView
from transcribe_ui.gui.settings.about import AboutViewModel
from transcribe_ui.gui.settings.about_view import AboutView

ACTION = "get_third_party_notices"
NOTICES_JSON = '{"action": "get_third_party_notices"}'
# Notices with non-ASCII text, as the generated file has (UTF-8).
NOTICES_TEXT = "FFmpeg — LGPL-2.1-or-later\n\nPySide6 © The Qt Company Ltd.\n"
LOADING = "Loading the third-party licences…"
FROM_SOURCE = "The third-party licences are included only in release builds."
FAILED = ErrorResponse(action=ACTION, error=ErrorBody(code=ERROR_INTERNAL, message="The notices cannot be read."))


@pytest.fixture
def backend(tmp_path: Path) -> Iterator[Backend]:
    """Return a seeded backend (closed afterwards)."""
    with closing(Backend(seeded_ports(tmp_path / "backend"))) as created:
        yield created


@pytest.fixture
def release_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the package data hold the notices, as in a release build."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / THIRD_PARTY_NOTICES_FILE).write_text(NOTICES_TEXT, encoding="utf-8")
    monkeypatch.setattr(info, "files", lambda _package: bundle)


def _about(executor: Recorded | Deferred, tmp_path: Path) -> AboutViewModel:
    return AboutViewModel(executor, tmp_path / "log")


def test_from_source_there_are_no_notices(tmp_path: Path) -> None:
    """Run from source, the backend and the CLI both answer `notices: null`."""
    exchange = run_one(tmp_path, NOTICES_JSON)
    assert exchange.exit_code == EXIT_OK
    assert_golden("get_third_party_notices.json", exchange.golden())


@pytest.mark.usefixtures("release_build")
def test_a_release_build_answers_its_notices(tmp_path: Path) -> None:
    """With the notices as package data, the backend and the CLI both answer their text unchanged."""
    exchange = run_one(tmp_path / "legs", NOTICES_JSON)
    response = response_adapter().validate_json(exchange.response)
    assert (exchange.exit_code, response) == (EXIT_OK, GetThirdPartyNoticesResponse(notices=NOTICES_TEXT))


@pytest.mark.usefixtures("release_build")
def test_the_notices_load_once(backend: Backend, tmp_path: Path) -> None:
    """Loading shows a line until the notices answer; once loaded they are kept (no second request)."""
    deferred = Deferred(backend)
    about = _about(deferred, tmp_path)
    assert (about.notices_state, about.notices_message()) == ("not_loaded", LOADING)
    about.load_notices()
    about.load_notices()
    assert (about.notices_state, about.notices_message(), deferred.actions()) == ("loading", LOADING, [ACTION])
    deferred.answer()
    assert (about.notices_state, about.notices, about.notices_message()) == ("loaded", NOTICES_TEXT, "")
    about.load_notices()
    assert deferred.actions() == []


def test_from_source_the_dialog_says_release_builds(backend: Backend, tmp_path: Path) -> None:
    """From source the notices are loaded as none, and the line says only release builds have them."""
    recorded = Recorded(backend)
    about = _about(recorded, tmp_path)
    about.load_notices()
    about.load_notices()
    assert (about.notices_state, about.notices, about.notices_message()) == ("loaded", None, FROM_SOURCE)
    assert recorded.actions() == [ACTION]


def test_a_failed_load_is_retried(backend: Backend, tmp_path: Path) -> None:
    """An error response shows its text; asking again (opening the dialog again) sends a new request."""
    answers: dict[str, Response] = {ACTION: FAILED}
    recorded = Recorded(Scripted(backend, answers))
    about = _about(recorded, tmp_path)
    about.load_notices()
    error = about.notices_error
    assert error is not None
    assert (about.notices_state, error.code, about.notices_message()) == ("failed", ERROR_INTERNAL, error.message)
    answers.clear()
    about.load_notices()
    assert (about.notices_state, about.notices_error, recorded.actions()) == ("loaded", None, [ACTION, ACTION])


def test_an_unexpected_answer_fails_and_is_retried(backend: Backend, tmp_path: Path) -> None:
    """An answer of another type is an internal error (not a silent reset); asking again sends a new request."""
    other = GetAppInfoResponse(app_version="test", contracts_schema_version="test")
    answers: dict[str, Response] = {ACTION: other}
    recorded = Recorded(Scripted(backend, answers))
    about = _about(recorded, tmp_path)
    about.load_notices()
    error = about.notices_error
    assert error is not None
    assert (about.notices_state, error.code, about.notices_message()) == ("failed", ERROR_INTERNAL, error.message)
    assert error.message
    answers.clear()
    about.load_notices()
    assert (about.notices_state, about.notices_error, recorded.actions()) == ("loaded", None, [ACTION, ACTION])


def _view(qtbot: QtBot, about: AboutViewModel, errors: list[ErrorView]) -> AboutView:
    view = AboutView(about, errors.append)
    qtbot.addWidget(view)
    view.show()
    return view


@pytest.mark.usefixtures("release_build")
def test_the_button_opens_the_notices(qtbot: QtBot, backend: Backend, tmp_path: Path) -> None:
    """The licences button opens a window-modal dialog with the notices, read-only, unwrapped, fixed-pitch."""
    recorded = Recorded(backend)
    view = _view(qtbot, _about(recorded, tmp_path), [])
    view.notices_button.click()
    dialog = view.notices_dialog
    edit = dialog.text_edit
    fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
    assert (dialog.isVisible(), dialog.windowTitle(), dialog.windowModality()) == (
        True,
        "Third-party licences",
        Qt.WindowModality.WindowModal,
    )
    assert (edit.toPlainText(), edit.isReadOnly(), edit.isVisible(), dialog.message_label.isVisible()) == (
        NOTICES_TEXT,
        True,
        True,
        False,
    )
    assert (edit.lineWrapMode(), edit.font().family()) == (QPlainTextEdit.LineWrapMode.NoWrap, fixed)
    dialog.reject()
    view.notices_button.click()
    assert (dialog.isVisible(), recorded.actions()) == (True, [ACTION])


def test_the_dialog_from_source(qtbot: QtBot, backend: Backend, tmp_path: Path) -> None:
    """From source the dialog says that only release builds include the licences."""
    view = _view(qtbot, _about(Recorded(backend), tmp_path), [])
    view.notices_button.click()
    dialog = view.notices_dialog
    shown = (dialog.message_label.text(), dialog.text_edit.isVisible(), dialog.details_button.isVisible())
    assert shown == (FROM_SOURCE, False, False)


def test_the_dialog_shows_a_failed_load(qtbot: QtBot, backend: Backend, tmp_path: Path) -> None:
    """A failed load shows the error's text; Details opens the error dialog with it."""
    errors: list[ErrorView] = []
    about = _about(Recorded(Scripted(backend, {ACTION: FAILED})), tmp_path)
    view = _view(qtbot, about, errors)
    view.notices_button.click()
    dialog = view.notices_dialog
    assert dialog.details_button.isVisible()
    dialog.details_button.click()
    assert (dialog.message_label.text(), errors) == (about.notices_message(), [about.notices_error])
