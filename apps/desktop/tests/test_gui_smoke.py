"""The GUI's smoke mode of release builds (plan m8 §1 "GUI smoke mode")."""

import logging
from functools import partial
from pathlib import Path
from types import MappingProxyType

import pytest
from pytestqt.qtbot import QtBot

from support.fakes import seeded_ports
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import Response
from transcribe_ui.api.errors import DomainError
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.api.routes import Route, default_routes
from transcribe_ui.gui import language
from transcribe_ui.gui.app import run_gui
from transcribe_ui.gui.constants import SMOKE_TEST_FAILED_EXIT_CODE, SMOKE_TEST_ON, SMOKE_TEST_VARIABLE
from transcribe_ui.runs.classes import ListRunsRequest

# Error code the failing start-up request answers with.
FAILING_CODE = "smoke_test_failure"


def _english() -> str:
    return "en"


def _hungarian() -> str:
    return "hu"


def _failing_list_runs(_request: ListRunsRequest, _context: RequestContext) -> Response:
    raise DomainError(FAILING_CODE, "The runs cannot be listed.")


def _backend(root: Path, *, failing: bool) -> Backend:
    routes = dict(default_routes())
    if failing:
        routes["list_runs"] = Route(run=_failing_list_runs, needs_database=True)
    return Backend(seeded_ports(root), MappingProxyType(routes))


@pytest.fixture
def smoke_mode(monkeypatch: pytest.MonkeyPatch, qtbot: QtBot) -> None:
    """Turn the smoke mode on (the GUI needs the `QApplication` of pytest-qt)."""
    assert qtbot is not None
    monkeypatch.setenv(SMOKE_TEST_VARIABLE, SMOKE_TEST_ON)


@pytest.mark.usefixtures("smoke_mode")
def test_smoke_mode_passes_when_all_answer_ok(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Every start-up request answers `ok`: the window closes itself (wizard open) and the exit code is 0."""
    caplog.set_level(logging.INFO)
    code = run_gui(partial(_backend, tmp_path, failing=False), os_language=_english)
    assert code == 0
    assert "Smoke test passed" in caplog.messages
    assert "Smoke test: list_runs answered ok" in caplog.messages


@pytest.mark.usefixtures("smoke_mode")
def test_smoke_mode_fails_on_an_error_answer(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A start-up request answers an error: the window still closes and the exit code is 1."""
    code = run_gui(partial(_backend, tmp_path, failing=True), os_language=_english)
    assert code == SMOKE_TEST_FAILED_EXIT_CODE
    assert any(FAILING_CODE in message for message in caplog.messages)


@pytest.mark.usefixtures("smoke_mode")
def test_smoke_mode_passes_with_qt_hungarian(tmp_path: Path) -> None:
    """Hungarian with Qt's own translation installed passes."""
    assert run_gui(partial(_backend, tmp_path, failing=False), os_language=_hungarian) == 0


@pytest.mark.usefixtures("smoke_mode")
def test_smoke_mode_fails_without_qt_hungarian(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hungarian without Qt's own translation (a build that lost `qtbase_hu.qm`) fails."""
    monkeypatch.setattr(language, "qt_translator", lambda _language: None)
    code = run_gui(partial(_backend, tmp_path, failing=False), os_language=_hungarian)
    assert code == SMOKE_TEST_FAILED_EXIT_CODE
