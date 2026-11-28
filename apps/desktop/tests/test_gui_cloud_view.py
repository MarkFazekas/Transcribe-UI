"""The Cloud page, the start dialog and the status bar line in the window (pytest-qt, offscreen; plan m6 M6.6).

The window runs over a launch world through `QtExecutor`: the start-up checks find a straggler
(banner, status bar link, Terminate through the confirmation dialog); the start dialog plans,
lists another computer's job with its button, and starts a run; closing then asks with the
cloud note; Stop is answered while the long pool is full of uploads.
"""

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from support.cloud import CloudWorld, cloud_world
from support.fakes import FIXED_NOW
from support.gated import POLL_S, upload, uploads, wait_for
from support.gui import window_over
from support.launch import FOREIGN_BATCH_ID
from support.runs import RUN_ID, app_pod_name
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import Request, Response
from transcribe_ui.api.ports import CancelToken, ProgressSink
from transcribe_ui.gui.cloud.status_view import StatusLine
from transcribe_ui.gui.cloud.view import CloudPage
from transcribe_ui.gui.constants import CALL_RUNNING, LONG_POOL_THREADS, PAGE_CLOUD
from transcribe_ui.gui.dialogs import CloseQuestion, ConfirmationDialog
from transcribe_ui.gui.executor import cancelled_response
from transcribe_ui.gui.shell import MainWindow


class HeldUploads:
    """The world's backend, except that uploads are held until released (they fill the long pool)."""

    def __init__(self, backend: Backend) -> None:
        """Answer every other request from `backend`."""
        self._backend = backend
        self._release = threading.Event()

    def execute(self, request: Request, progress: ProgressSink | None, cancel: CancelToken | None) -> Response:
        """Hold an upload until `release` (or its cancel flag); run anything else."""
        if request.action != "run_upload":
            return self._backend.execute(request, progress, cancel)
        while not self._release.wait(POLL_S):
            if cancel is not None and cancel.is_cancelled():
                return cancelled_response(request)
        return cancelled_response(request)

    def release(self) -> None:
        """Let the held uploads end."""
        self._release.set()


@pytest.fixture
def world(tmp_path: Path) -> Iterator[CloudWorld]:
    """Return a launch leg: the held batch and another computer's job wait."""
    with cloud_world(tmp_path) as created:
        yield created


def _drained(qtbot: QtBot, window: MainWindow) -> None:
    # The tests drain first: pytest-qt deletes the window's widgets before the fixtures end.
    wait_for(qtbot, lambda: not window.busy())
    # Answers still on their way must not reach the widgets pytest-qt deletes next.
    window.context.executor.close()


@pytest.fixture
def held(world: CloudWorld) -> HeldUploads:
    """Return the world's backend with held uploads."""
    return HeldUploads(world.backend)


@pytest.fixture
def window(qtbot: QtBot, held: HeldUploads, tmp_path: Path) -> Iterator[MainWindow]:
    """Return the window over the world (a test runs the start-up steps); drained afterwards."""
    created = window_over(qtbot, held, tmp_path)
    yield created
    held.release()
    _drained(qtbot, created)


def _page(window: MainWindow) -> CloudPage:
    page = window.pages.page(PAGE_CLOUD)
    assert isinstance(page, CloudPage)
    return page


def _status_line(window: MainWindow) -> StatusLine:
    line = window.statusBar().findChild(StatusLine)
    assert isinstance(line, StatusLine)
    return line


def _confirm(qtbot: QtBot, window: MainWindow) -> None:
    wait_for(qtbot, lambda: isinstance(window.dialog, ConfirmationDialog) and window.dialog.isVisible())
    dialog = window.dialog
    assert isinstance(dialog, ConfirmationDialog)
    dialog.confirm_button.click()


def _start_a_run(qtbot: QtBot, window: MainWindow) -> None:
    window.context.hooks.run()
    page = _page(window)
    page.run_panel.buttons.start_button.click()
    dialog = page.start_dialog
    wait_for(qtbot, dialog.start_button.isEnabled)
    dialog.start_button.click()
    wait_for(qtbot, lambda: not dialog.isVisible() and page.history.runs.run_active())


def test_a_straggler_at_start_up(qtbot: QtBot, world: CloudWorld, window: MainWindow) -> None:
    """The start-up check shows the banner and a status bar link; Terminate (confirmed) clears both."""
    world.cloud.api.pods.add(app_pod_name(RUN_ID, FIXED_NOW))
    window.context.hooks.run()
    page = _page(window)
    machines = page.machines
    wait_for(qtbot, lambda: machines.model.attention and not window.busy())
    assert not page.attention.isHidden()
    line = _status_line(window)
    assert [shown.target for shown in line.shown] == ["machines"]
    line.linkActivated.emit("machines:")
    assert window.pages.current_page() == PAGE_CLOUD
    buttons = list(machines.stop_buttons.values())
    assert [button.text() for button in buttons] == ["Terminate"]
    button = buttons[0]
    button.click()
    _confirm(qtbot, window)
    wait_for(qtbot, lambda: not machines.model.attention and machines.model.checked and not window.busy())
    assert (page.attention.isHidden(), line.shown) == (True, ())
    wait_for(qtbot, lambda: not window.busy())


def test_the_start_dialog_starts_a_run(qtbot: QtBot, window: MainWindow) -> None:
    """The dialog plans, lists another computer's job with "Delete from cloud" (emitting its signal), and starts."""
    deleted: list[str] = []
    window.context.signals.delete_unknown_batch.connect(deleted.append)
    window.context.hooks.run()
    page = _page(window)
    page.run_panel.buttons.start_button.click()
    dialog = page.start_dialog
    wait_for(qtbot, dialog.start_button.isEnabled)
    assert dialog.isVisible()
    dialog.plan.delete_buttons[FOREIGN_BATCH_ID].click()
    assert deleted == [FOREIGN_BATCH_ID]
    dialog.start_button.click()
    wait_for(qtbot, lambda: not dialog.isVisible() and page.history.runs.run_active())
    panel = page.run_panel
    assert (panel.buttons.stop_button.text(), panel.buttons.start_button.isEnabled()) == ("Stop", False)
    assert "Cloud: Starting" in _status_line(window).text()
    wait_for(qtbot, lambda: not window.busy())


def test_closing_during_a_run_adds_the_cloud_note(qtbot: QtBot, window: MainWindow) -> None:
    """With a run that is not final, closing asks first and says that the machine keeps working."""
    _start_a_run(qtbot, window)
    wait_for(qtbot, lambda: not window.busy())
    assert not window.close()
    question = window.closing.question
    assert isinstance(question, CloseQuestion)
    texts = [label.text() for label in question.findChildren(QLabel)]
    assert any(text.startswith("The cloud machine keeps working without the app") for text in texts)
    question.reject()


def test_stop_is_answered_while_the_long_pool_is_busy(qtbot: QtBot, held: HeldUploads, window: MainWindow) -> None:
    """Uploads fill every long thread; Stop (short pool) still asks and stops the run."""
    _start_a_run(qtbot, window)
    executor = window.context.executor
    uploading = [executor.submit(request, lambda _response: None) for request in uploads(LONG_POOL_THREADS)]
    queued = executor.submit(upload(), lambda _response: None)
    wait_for(qtbot, lambda: all(call.state == CALL_RUNNING for call in uploading))
    page = _page(window)
    page.run_panel.buttons.stop_button.click()
    _confirm(qtbot, window)
    runs = page.history.runs
    wait_for(qtbot, lambda: runs.current is not None and runs.current.stage == "cancelled")
    assert all(call.state == CALL_RUNNING for call in uploading)
    queued.cancel()
    held.release()
    wait_for(qtbot, lambda: not window.busy())
