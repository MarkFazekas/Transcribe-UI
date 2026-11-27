"""`run_gui` ends cleanly when its event loop stops with requests still in flight (plan m6 M6.2)."""

import threading
from contextlib import ExitStack
from functools import partial
from pathlib import Path
from types import MappingProxyType

import pytest
import shiboken6
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from support.fakes import seeded_ports
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import GetAppInfoRequest, Response
from transcribe_ui.api.info import get_app_info
from transcribe_ui.api.ports import RequestContext
from transcribe_ui.api.routes import Route, default_routes
from transcribe_ui.gui import app as gui_app
from transcribe_ui.gui.app import run_gui
from transcribe_ui.gui.closing import QuitNow
from transcribe_ui.gui.shell import MainWindow

# How long a held request waits at most, in seconds (a safety net; the tests release it sooner).
HOLD_LIMIT_S = 30
# How often a held request looks at its release and its cancel flag, in seconds.
HOLD_POLL_S = 0.01
# How long run_gui waits for running requests in the test that lets one outlive the wait, in ms.
SHORT_WAIT_MS = 200


class _Hold:
    """A `get_app_info` handler that runs until released, or until cancelled if it honours that."""

    def __init__(self, *, honour_cancel: bool) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self._honour_cancel = honour_cancel

    def __call__(self, request: GetAppInfoRequest, context: RequestContext) -> Response:
        self.started.set()
        for _ in range(int(HOLD_LIMIT_S / HOLD_POLL_S)):
            if self.release.wait(HOLD_POLL_S) or (self._honour_cancel and context.cancel.is_cancelled()):
                break
        context.check_cancelled()
        return get_app_info(request, context)


class _Quits:
    """The ports of "Quit now", recorded instead of ending the test process."""

    def __init__(self) -> None:
        self.codes: list[int] = []

    def flush(self) -> None:
        """Flush nothing: the test keeps its log handlers."""

    def end(self, code: int) -> None:
        self.codes.append(code)


def _backend(root: Path, hold: _Hold) -> Backend:
    routes = dict(default_routes())
    routes["get_app_info"] = Route(run=hold, needs_database=False)
    return Backend(seeded_ports(root), MappingProxyType(routes))


def _quit_while_held(hold: _Hold, window: MainWindow, shown: list[MainWindow] | None = None) -> None:
    if shown is not None:
        shown.append(window)
    window.context.executor.submit(GetAppInfoRequest(action="get_app_info"), lambda _response: None)
    hold.started.wait(HOLD_LIMIT_S)
    # The loop ends without the close dialog (`exit`, unlike `quit`, asks no window to close).
    QTimer.singleShot(0, partial(QApplication.exit, 0))


def test_loop_end_cancels_running_requests(qtbot: QtBot, tmp_path: Path) -> None:
    """The loop ends with a request running: it is cancelled and waited for; the process is not ended."""
    assert qtbot is not None
    hold = _Hold(honour_cancel=True)
    quits = _Quits()
    shown: list[MainWindow] = []
    on_window = partial(_quit_while_held, hold, shown=shown)
    code = run_gui(partial(_backend, tmp_path, hold), quit_now=QuitNow(quits.flush, quits.end), on_window=on_window)
    assert (code, quits.codes) == (0, [])
    # The window was deleted before `run_gui` returned (no event loop would run `deleteLater`).
    assert [shiboken6.isValid(window) for window in shown] == [False]


def test_requests_outliving_the_wait_end_the_process(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A request that ignores its flag past the bounded wait: the log is flushed and the process ends."""
    assert qtbot is not None
    monkeypatch.setattr(gui_app, "SHUTDOWN_WAIT_MS", SHORT_WAIT_MS)
    hold = _Hold(honour_cancel=False)
    quits = _Quits()
    on_window = partial(_quit_while_held, hold)
    with ExitStack() as cleanup:
        # The held request ends with the test either way.
        cleanup.callback(hold.release.set)
        run_gui(partial(_backend, tmp_path, hold), quit_now=QuitNow(quits.flush, quits.end), on_window=on_window)
    assert quits.codes == [0]
