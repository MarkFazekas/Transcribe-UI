"""The two executables (A20): the GUI entry point, its display check and log; the CLI never loads Qt."""

import ast
import io
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer, qWarning
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

import transcribe_ui
from transcribe_ui.api.classes import ErrorResponse
from transcribe_ui.api.constants import ERROR_DISPLAY_UNAVAILABLE
from transcribe_ui.api.schema import response_adapter
from transcribe_ui.cli.constants import EXIT_ERROR
from transcribe_ui.gui import main as gui_main
from transcribe_ui.gui.constants import (
    FORCED_PLATFORM_MESSAGE,
    GUI_LOG_FILE_NAME,
    NO_DISPLAY_MESSAGE,
    QT_PLATFORM_VARIABLE,
    WAYLAND_DISPLAY_VARIABLE,
    X11_DISPLAY_VARIABLE,
)
from transcribe_ui.gui.display import display_available, display_problem
from transcribe_ui.gui.messages import qt_messages_to_log
from transcribe_ui.gui.shell import MainWindow
from transcribe_ui.state.logs import log_to_file
from transcribe_ui.state.paths import AppPaths

SUBPROCESS_TIMEOUT_S = 60
# The app's package; every module outside `gui/` must stay free of Qt.
PACKAGE_DIR = Path(transcribe_ui.__file__).resolve().parent
# Module names no module outside `gui/` may import (the CLI executable ships without Qt).
GUI_ONLY = ("PySide6", "shiboken6", "transcribe_ui.gui")
LINUX = "linux"
# How often the test looks for an idle window to close, in milliseconds.
IDLE_CHECK_MS = 50
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


def _environ(forced: str, *, x11: bool, wayland: bool) -> dict[str, str]:
    environ = {QT_PLATFORM_VARIABLE: forced} if forced else {}
    if x11:
        environ[X11_DISPLAY_VARIABLE] = ":0"
    if wayland:
        environ[WAYLAND_DISPLAY_VARIABLE] = "wayland-0"
    return environ


# Imports the GUI entry module and runs the display check, then fails if Qt was loaded on the way.
GUI_ENTRY_WITHOUT_QT = """
import sys
import transcribe_ui.gui.main
from transcribe_ui.gui.display import display_problem
display_problem({}, "linux")
loaded = [name for name in sys.modules if name.startswith(("PySide6", "shiboken6"))]
assert not loaded, loaded
"""


@pytest.mark.parametrize(
    ("forced", "x11", "wayland", "platform", "expected"),
    [
        ("", False, False, "win32", True),
        ("", False, False, "darwin", True),
        ("", False, False, LINUX, False),
        ("", True, False, LINUX, True),
        ("", False, True, LINUX, True),
        ("offscreen", False, False, LINUX, True),
        ("minimal:enable_fonts", False, False, "freebsd14", True),
        ("xcb", False, True, LINUX, False),
        ("xcb", True, False, LINUX, True),
        ("wayland-egl", True, False, LINUX, False),
        ("wayland;xcb", True, False, LINUX, True),
        ("eglfs", False, False, LINUX, False),
        ("linuxfb", True, False, LINUX, False),
        ("eglfs;offscreen", False, False, LINUX, True),
    ],
)
def test_display_check(forced: str, x11: bool, wayland: bool, platform: str, expected: bool) -> None:  # noqa: FBT001 -- pytest parameters
    """Windows and macOS always have a display; elsewhere the forced platform needs what it uses."""
    assert display_available(_environ(forced, x11=x11, wayland=wayland), platform) is expected


def test_gui_entry_checks_the_display_without_qt() -> None:
    """The GUI entry module (with the display check and the constants) loads no Qt before the check."""
    completed = subprocess.run(  # noqa: S603 -- the test interpreter on a fixed script
        [sys.executable, "-c", GUI_ENTRY_WITHOUT_QT],
        capture_output=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_cli_never_loads_qt() -> None:
    """The CLI executable's modules, `schema` and a request load no PySide6 and no GUI module."""
    completed = subprocess.run(  # noqa: S603 -- the test interpreter on a fixed script
        [sys.executable, "-c", CLI_WITHOUT_QT],
        capture_output=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def _node_imports(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        return [] if node.module is None else [node.module]
    if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
        # A module named to `importlib.import_module` (or any call taking a constant first).
        return [str(node.args[0].value)]
    return []


def _imported(tree: ast.AST) -> list[str]:
    """Return every module the tree imports: statements at any depth and constant `import_module` names."""
    return [name for node in ast.walk(tree) for name in _node_imports(node)]


def _gui_only(name: str) -> bool:
    return any(name == banned or name.startswith(f"{banned}.") for banned in GUI_ONLY)


def test_no_module_outside_gui_imports_qt() -> None:
    """No module outside `gui/` imports PySide6, shiboken6 or the GUI, at any nesting level."""
    found: list[str] = []
    for module in sorted(PACKAGE_DIR.rglob("*.py")):
        relative = module.relative_to(PACKAGE_DIR)
        if relative.parts[0] == "gui":
            continue
        tree = ast.parse(module.read_text(encoding="utf-8"), str(module))
        found.extend(f"{relative}: {name}" for name in _imported(tree) if _gui_only(name))
    assert not found


def test_no_display_answers_display_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No display on Linux: the GUI entry logs why, prints the `display_unavailable` JSON and exits 1."""
    for name in (X11_DISPLAY_VARIABLE, WAYLAND_DISPLAY_VARIABLE, QT_PLATFORM_VARIABLE):
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
    assert response.error.message == NO_DISPLAY_MESSAGE
    assert NO_DISPLAY_MESSAGE in log_file.read_text(encoding="utf-8")


def test_a_forced_platform_is_named_in_the_message() -> None:
    """A forced platform that cannot start is named, with what it would need."""
    problem = display_problem({QT_PLATFORM_VARIABLE: "xcb"}, LINUX)
    assert problem == FORCED_PLATFORM_MESSAGE.format(platforms="xcb")


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
