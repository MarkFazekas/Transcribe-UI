"""The GUI's smoke mode of release builds (plan m8 §1 "GUI smoke mode")."""

import logging
from functools import partial
from pathlib import Path
from types import MappingProxyType

import pytest
from PySide6.QtCore import QTimer, qCritical
from pytestqt.qtbot import QtBot

from support.fakes import seeded_ports
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import Response
from transcribe_ui.api.errors import DomainError
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.api.routes import Route, default_routes
from transcribe_ui.gui import app as gui_app
from transcribe_ui.gui import language
from transcribe_ui.gui.app import run_gui
from transcribe_ui.gui.constants import SMOKE_TEST_FAILED_EXIT_CODE, SMOKE_TEST_ON, SMOKE_TEST_VARIABLE
from transcribe_ui.gui.context import GuiContext
from transcribe_ui.gui.shell import MainWindow, build_shell
from transcribe_ui.runs.classes import ListRunsRequest

# Message of the exception a slot raises during start-up.
SLOT_FAILURE = "a slot failed during start-up"
# A critical message of Qt during start-up.
QT_CRITICAL = "a critical message of Qt during start-up"
# Error code the failing start-up request answers with.
FAILING_CODE = "smoke_test_failure"
# Runs a test with the smoke mode turned on (the `smoke_mode` fixture).
in_smoke_mode = pytest.mark.usefixtures("smoke_mode")


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


@in_smoke_mode
def test_smoke_mode_passes_when_all_answer_ok(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Every start-up request answers `ok`: the window closes itself (wizard open) and the exit code is 0."""
    caplog.set_level(logging.INFO)
    code = run_gui(partial(_backend, tmp_path, failing=False), os_language=_english)
    assert code == 0
    assert "Smoke test passed" in caplog.messages
    assert "Smoke test: list_runs answered ok" in caplog.messages


@in_smoke_mode
def test_smoke_mode_fails_on_an_error_answer(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A start-up request answers an error: the window still closes and the exit code is 1."""
    code = run_gui(partial(_backend, tmp_path, failing=True), os_language=_english)
    assert code == SMOKE_TEST_FAILED_EXIT_CODE
    assert any(FAILING_CODE in message for message in caplog.messages)


@in_smoke_mode
def test_smoke_mode_passes_with_qt_hungarian(tmp_path: Path) -> None:
    """Hungarian with Qt's own translation installed passes."""
    assert run_gui(partial(_backend, tmp_path, failing=False), os_language=_hungarian) == 0


@in_smoke_mode
def test_smoke_mode_fails_without_qt_hungarian(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hungarian without Qt's own translation (a build that lost `qtbase_hu.qm`) fails."""
    monkeypatch.setattr(language, "qt_translator", lambda _language: None)
    code = run_gui(partial(_backend, tmp_path, failing=False), os_language=_hungarian)
    assert code == SMOKE_TEST_FAILED_EXIT_CODE


def _raise_in_a_slot() -> None:
    raise RuntimeError(SLOT_FAILURE)


def _critical_message() -> None:
    qCritical(QT_CRITICAL)


def _shell_with_a_failing_slot(context: GuiContext) -> MainWindow:
    window = build_shell(context)
    # Runs on the event loop during start-up, like any slot; its exception goes to the log only.
    QTimer.singleShot(0, window, _raise_in_a_slot)
    return window


def _shell_with_a_critical_message(context: GuiContext) -> MainWindow:
    window = build_shell(context)
    # The Qt message handler of `run_gui` sends it to the qt logger as an ERROR record.
    QTimer.singleShot(0, window, _critical_message)
    return window


@in_smoke_mode
def test_smoke_mode_fails_when_a_slot_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An exception escaping a Qt slot is only logged, yet it fails the smoke run; the window still closes."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(gui_app, "build_shell", _shell_with_a_failing_slot)
    code = run_gui(partial(_backend, tmp_path, failing=False), os_language=_english)
    assert code == SMOKE_TEST_FAILED_EXIT_CODE
    # The traceback of the slot's exception is in the log, and the smoke test failed on that record.
    assert SLOT_FAILURE in caplog.text
    assert "logged ERROR by transcribe_ui.gui.messages" in caplog.text
    assert "Smoke test: closing the window" in caplog.messages


@in_smoke_mode
def test_smoke_mode_fails_on_a_critical_qt_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A critical message of Qt reaches the log through the qt logger and fails the smoke run."""
    monkeypatch.setattr(gui_app, "build_shell", _shell_with_a_critical_message)
    code = run_gui(partial(_backend, tmp_path, failing=False), os_language=_english)
    assert code == SMOKE_TEST_FAILED_EXIT_CODE
    assert f"logged ERROR by transcribe_ui.gui.qt: {QT_CRITICAL}" in caplog.text
