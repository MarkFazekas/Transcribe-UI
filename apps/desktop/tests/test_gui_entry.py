"""The two executables (A20): the GUI entry point, its display check and log; the CLI never loads Qt."""

import io
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer, qWarning
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from transcribe_ui.api.classes import ErrorResponse
from transcribe_ui.api.constants import ERROR_DISPLAY_UNAVAILABLE
from transcribe_ui.api.schema import response_adapter
from transcribe_ui.cli.constants import EXIT_ERROR
from transcribe_ui.gui import main as gui_main
from transcribe_ui.gui.constants import DISPLAY_UNAVAILABLE_MESSAGE, GUI_LOG_FILE_NAME, IDLE_CHECK_MS
from transcribe_ui.gui.display import display_available
from transcribe_ui.gui.messages import qt_messages_to_log
from transcribe_ui.gui.shell import MainWindow
from transcribe_ui.state.logs import log_to_file
from transcribe_ui.state.paths import AppPaths

SUBPROCESS_TIMEOUT_S = 60
LINUX = "linux"
# Imports what the CLI executable runs, runs `schema` and a request that needs no database, then
# fails if any Qt or GUI module was loaded on the way.
CLI_WITHOUT_QT = """
import importlib, io, sys, tempfile
from pathlib import Path
import transcribe_ui.cli.main as cli
import transcribe_ui.api.routes as routes
for route in routes.default_routes().values():
    importlib.import_module(route.run.__module__)
assert cli.main(["schema"], stdout=io.BytesIO()) == 0
assert cli.main(["--version"], stdout=io.BytesIO()) == 0
with tempfile.TemporaryDirectory() as folder:
    request = Path(folder) / "request.json"
    request.write_text('{"action": "get_app_info"}', encoding="utf-8")
    assert cli.main(["run", str(request)], stdout=io.BytesIO(), stderr=io.BytesIO()) == 0
loaded = [name for name in sys.modules if name.startswith(("PySide6", "shiboken6", "transcribe_ui.gui"))]
assert not loaded, loaded
"""


@pytest.mark.parametrize(
    ("environ", "platform", "expected"),
    [
        ({}, "win32", True),
        ({}, "darwin", True),
        ({}, LINUX, False),
        ({"DISPLAY": ":0"}, LINUX, True),
        ({"WAYLAND_DISPLAY": "wayland-0"}, LINUX, True),
        ({"QT_QPA_PLATFORM": "offscreen"}, LINUX, True),
        ({"QT_QPA_PLATFORM": "minimal:enable_fonts"}, "freebsd14", True),
        ({"QT_QPA_PLATFORM": "wayland;xcb"}, LINUX, False),
        ({"QT_QPA_PLATFORM": "xcb;offscreen"}, LINUX, True),
    ],
)
def test_display_check(environ: dict[str, str], platform: str, expected: bool) -> None:  # noqa: FBT001 -- pytest parameters
    """Windows and macOS always have a display; elsewhere a display server or a headless Qt platform."""
    assert display_available(environ, platform) is expected


def test_cli_never_loads_qt() -> None:
    """The CLI executable's modules, `schema` and a request load no PySide6 and no GUI module."""
    completed = subprocess.run(  # noqa: S603 -- the test interpreter on a fixed script
        [sys.executable, "-c", CLI_WITHOUT_QT],
        capture_output=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_no_display_answers_display_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No display on Linux: the GUI entry logs why, prints the `display_unavailable` JSON and exits 1."""
    for name in ("DISPLAY", "WAYLAND_DISPLAY", "QT_QPA_PLATFORM"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "platform", LINUX)
    stdout = io.BytesIO()
    log_file = tmp_path / GUI_LOG_FILE_NAME
    code = gui_main.main(log_file=log_file, stdout=stdout)
    monkeypatch.undo()
    assert code == EXIT_ERROR
    response = response_adapter().validate_json(stdout.getvalue())
    assert isinstance(response, ErrorResponse)
    assert response.error.code == ERROR_DISPLAY_UNAVAILABLE
    assert DISPLAY_UNAVAILABLE_MESSAGE in log_file.read_text(encoding="utf-8")


def test_a_qt_warning_lands_in_the_log(tmp_path: Path, qtbot: QtBot) -> None:
    """Messages of Qt go to the GUI log, not to stderr."""
    assert qtbot is not None
    log_file = tmp_path / GUI_LOG_FILE_NAME
    with log_to_file(log_file), qt_messages_to_log():
        qWarning("a warning from Qt")
    assert "a warning from Qt" in log_file.read_text(encoding="utf-8")


def _close_when_idle() -> None:
    windows = [widget for widget in QApplication.topLevelWidgets() if isinstance(widget, MainWindow)]
    for window in windows:
        if window.isVisible() and not window.busy():
            window.close()
            return
    QTimer.singleShot(IDLE_CHECK_MS, _close_when_idle)


def test_the_gui_entry_point_opens_the_window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, qtbot: QtBot) -> None:
    """`transcribe-ui-gui` opens the window (offscreen), runs the start-up probe and exits 0 once closed."""
    assert qtbot is not None
    monkeypatch.setattr(AppPaths, "for_user", classmethod(lambda _cls: AppPaths.under(tmp_path)))
    QTimer.singleShot(IDLE_CHECK_MS, _close_when_idle)
    with pytest.raises(SystemExit) as exited:
        gui_main.run()
    assert exited.value.code == 0
    paths = AppPaths.under(tmp_path)
    assert paths.database_file.is_file()
    log_file = paths.log_dir / GUI_LOG_FILE_NAME
    assert not log_file.exists() or "Traceback" not in log_file.read_text(encoding="utf-8")
