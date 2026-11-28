"""The main window: pages, fallbacks, dialogs, the activity panel, the writer-lock notice, closing (plan m6 M6.2)."""

import logging
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import ExitStack, closing
from pathlib import Path

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from support.fakes import seeded_ports
from support.gated import HELD_STAGE, UPLOAD, GatedRunner, listing, upload, uploads, wait_for
from support.gui import window_over
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import Confirmation, ErrorBody, ErrorResponse, NeedsConfirmation, Request, Response
from transcribe_ui.api.ports import CancelToken, ProgressSink
from transcribe_ui.gui.activity_view import ActivityRow
from transcribe_ui.gui.closing import QuitNow, flush_log_handlers
from transcribe_ui.gui.cloud.view import CloudPage
from transcribe_ui.gui.constants import (
    LONG_POOL_THREADS,
    PAGE_CLOUD,
    PAGE_JOBS,
    PAGE_NEW_JOB,
    PAGE_SETTINGS,
    PAGES,
)
from transcribe_ui.gui.dialogs import ErrorDialog
from transcribe_ui.gui.feedback import confirmation_view, error_view
from transcribe_ui.gui.jobs.view import JobsPage
from transcribe_ui.gui.selection.view import NewJobPage
from transcribe_ui.gui.settings.view import SettingsPage
from transcribe_ui.gui.shell import MainWindow
from transcribe_ui.gui.texts import activity_state_text
from transcribe_ui.state.logs import log_to_file

TESTS_DIR = Path(__file__).resolve().parent
SUBPROCESS_TIMEOUT_S = 60
BATCH = "batch_20261127_120000"
# `list_batches` sent once the writer lock is free again: the probe, then the jobs list's reload.
PROBE_AND_RELOAD = 2


@pytest.fixture
def gated() -> GatedRunner:
    """Return a runner that holds every `run_upload` until released."""
    return GatedRunner((UPLOAD,))


@pytest.fixture
def window(qtbot: QtBot, gated: GatedRunner, tmp_path: Path) -> Iterator[MainWindow]:
    """Return the window over the gated runner; released and drained afterwards."""
    created = window_over(qtbot, gated, tmp_path)
    yield created
    gated.release()
    wait_for(qtbot, lambda: not created.busy())


def _error(code: str, details: dict[str, object] | None = None) -> ErrorResponse:
    return ErrorResponse(action="plan_launch", error=ErrorBody(code=code, message="It failed.", details=details or {}))


def test_the_shell_shows_the_four_pages(window: MainWindow) -> None:
    """New job, Jobs, Cloud and Settings are each their area's page (no placeholder is left)."""
    assert window.pages.navigation.count() == len(PAGES)
    assert window.pages.current_page() == PAGE_NEW_JOB
    pages = (PAGE_JOBS, PAGE_SETTINGS, PAGE_NEW_JOB, PAGE_CLOUD)
    built = tuple(type(window.pages.page(key)) for key in pages)
    assert built == (JobsPage, SettingsPage, NewJobPage, CloudPage)


def test_navigation_signals_open_their_pages(window: MainWindow) -> None:
    """Navigation signals select the page (the areas add their sub-views later)."""
    signals = window.context.signals
    signals.open_settings.emit("accounts")
    assert window.pages.current_page() == PAGE_SETTINGS
    signals.open_job.emit(BATCH)
    assert window.pages.current_page() == PAGE_JOBS
    signals.open_cloud_machines.emit()
    assert window.pages.current_page() == PAGE_CLOUD


def test_unhandled_actions_say_not_available(window: MainWindow) -> None:
    """An action signal without a handler shows "Not available yet"; a connected one reaches its handler."""
    signals = window.context.signals
    signals.open_review.emit(BATCH)
    assert window.statusBar().currentMessage() == "Not available yet."
    window.statusBar().clearMessage()
    received: list[str] = []
    window.connect_handler("open_review", received.append)
    signals.open_review.emit(BATCH)
    assert received == [BATCH]
    assert not window.statusBar().currentMessage()


def test_the_error_dialog_fix_opens_the_named_batch(window: MainWindow) -> None:
    """`cleanup_pending`'s fix opens the job page of the batch named in the details."""
    opened: list[str] = []
    window.context.signals.open_job.connect(opened.append)
    dialog = window.show_error(error_view(_error("cleanup_pending", {"batch_ids": [BATCH]})))
    assert dialog.fix_button is not None
    dialog.fix_button.click()
    assert opened == [BATCH]
    assert window.pages.current_page() == PAGE_JOBS


def test_the_error_dialog_retries_app_busy(window: MainWindow, gated: GatedRunner, qtbot: QtBot) -> None:
    """`app_busy`'s fix re-sends the failed action and probes the lock, so the notice clears too."""
    lock = window.context.lock
    lock.note(listing(), _error("app_busy"))
    assert lock.state == "busy"
    retried: list[bool] = []
    dialog = window.show_error(error_view(_error("app_busy")), lambda: retried.append(True))
    assert not dialog.details.isVisibleTo(dialog)
    dialog.details_button.click()
    assert dialog.details.isVisibleTo(dialog)
    assert dialog.fix_button is not None
    dialog.fix_button.click()
    wait_for(qtbot, lambda: lock.state == "held")
    # The probe, then the jobs list's reload once the lock is held again.
    wait_for(qtbot, lambda: gated.started("list_batches") == PROBE_AND_RELOAD)
    assert retried == [True]


def test_app_busy_without_a_retry_probes_the_lock(window: MainWindow, gated: GatedRunner, qtbot: QtBot) -> None:
    """With no request to re-send (an activity's Details), `app_busy`'s Retry probes the writer lock."""
    dialog = window.show_error(error_view(_error("app_busy")))
    assert dialog.fix_button is not None
    dialog.fix_button.click()
    wait_for(qtbot, lambda: gated.started("list_batches") == 1)


class _BusyFirst:
    """Answers its first call `app_busy`, later ones with app info (another process let go)."""

    def __init__(self) -> None:
        self.sent: list[Request] = []

    def execute(self, request: Request, progress: ProgressSink | None, cancel: CancelToken | None) -> Response:
        self.sent.append(request)
        if len(self.sent) == 1:
            return _error("app_busy")
        return GatedRunner().execute(request, progress, cancel)


def test_activity_retry_resumes_the_entry(qtbot: QtBot, tmp_path: Path) -> None:
    """An activity that failed with `app_busy`: Details, then Retry re-sends its request."""
    runner = _BusyFirst()
    window = window_over(qtbot, runner, tmp_path)
    entry = window.context.activity.start(upload(), "Upload")
    assert entry is not None
    wait_for(qtbot, lambda: entry.state == "failed" and len(window.activity_panel.rows) == 1)
    details = window.activity_panel.rows[0].details_button
    wait_for(qtbot, lambda: details.isVisibleTo(window))
    details.click()
    dialog = window.dialog
    assert isinstance(dialog, ErrorDialog)
    assert dialog.fix_button is not None
    dialog.fix_button.click()
    wait_for(qtbot, lambda: entry.state == "finished")
    # The re-send (long pool) and the lock probe (short pool) run side by side, in any order; the
    # jobs list and the run list reload once the probe got the lock.
    wait_for(qtbot, lambda: len(runner.sent) == PROBE_AND_RELOAD + 3)
    sent = sorted(request.action for request in runner.sent)
    assert sent == sorted([UPLOAD, UPLOAD, "list_batches", "list_batches", "list_runs"])


def test_the_confirmation_dialog_confirms(window: MainWindow) -> None:
    """Confirm calls back the caller, which re-sends the confirmed request."""
    confirmation = Confirmation(code="test_delete", field="confirm", set_to=True, message="Delete?")
    asked = NeedsConfirmation(action="cleanup_cloud", confirmations=(confirmation,))
    confirmed: list[bool] = []
    dialog = window.show_confirmation(confirmation_view(asked), lambda: confirmed.append(True))
    dialog.confirm_button.click()
    assert confirmed == [True]


def _row(window: MainWindow) -> ActivityRow:
    rows = window.activity_panel.rows
    assert len(rows) == 1
    return rows[0]


def _status(window: MainWindow) -> str:
    return _row(window).status.text()


def test_activity_panel_cancels_and_resumes(window: MainWindow, gated: GatedRunner, qtbot: QtBot) -> None:
    """Cancel stops a long call (shown neutral, with Resume); Resume sends the same request again."""
    entry = window.context.activity.start(upload(), "Upload")
    assert entry is not None
    wait_for(qtbot, lambda: entry.state == "running" and _row(window).cancel_button.isVisibleTo(window))
    _row(window).cancel_button.click()
    wait_for(qtbot, lambda: _status(window) == activity_state_text("stopped"))
    _row(window).resume_button.click()
    wait_for(qtbot, lambda: gated.started(UPLOAD) == len(("first", "resumed")))
    gated.release()
    wait_for(qtbot, lambda: _status(window) == activity_state_text("finished"))
    assert gated.requests[0] == gated.requests[1]


def _progress_count(window: MainWindow) -> int:
    status = _status(window)
    if not status.startswith(HELD_STAGE):
        return -1
    return int(status.split()[-1])


def test_activity_rows_survive_progress(qtbot: QtBot, tmp_path: Path) -> None:
    """Progress repaints update the row in place: the Cancel button shown first is the one clicked."""
    runner = GatedRunner((UPLOAD,), report=True)
    window = window_over(qtbot, runner, tmp_path)
    window.context.activity.start(upload(), "Upload")
    wait_for(qtbot, lambda: _progress_count(window) >= 0)
    cancel_button = _row(window).cancel_button
    first = _progress_count(window)
    wait_for(qtbot, lambda: _progress_count(window) > first + 1)
    assert _row(window).cancel_button is cancel_button
    cancel_button.click()
    wait_for(qtbot, lambda: _status(window) == activity_state_text("stopped"))
    assert _row(window).cancel_button is cancel_button


def test_close_with_nothing_in_flight_closes_at_once(window: MainWindow) -> None:
    """No call and no active run: the window closes without a question."""
    assert window.close()
    assert window.closing.question is None


def test_close_stops_calls_and_waits_for_them(window: MainWindow, gated: GatedRunner, qtbot: QtBot) -> None:
    """Stop and close: queued calls never run, running ones see their flag, then the window closes."""
    for request in uploads(LONG_POOL_THREADS + 1):
        window.context.executor.submit(request, lambda _response: None)
    wait_for(qtbot, lambda: gated.started(UPLOAD) == LONG_POOL_THREADS)
    _stop_and_close(window)
    wait_for(qtbot, lambda: not window.isVisible())
    assert gated.cancelled == [UPLOAD for _ in range(LONG_POOL_THREADS)]
    assert gated.started(UPLOAD) == LONG_POOL_THREADS


def _never_start(_pool: QThreadPool, _runnable: object) -> None:
    """Keep every call queued: the pool never runs it."""


def test_close_with_only_queued_calls_skips_the_wait(
    window: MainWindow,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calls that never left their queue are cancelled at once: no Finishing dialog, the window closes."""
    monkeypatch.setattr(QThreadPool, "start", _never_start)
    window.context.executor.submit(upload(), lambda _response: None)
    monkeypatch.undo()
    _stop_and_close(window)
    wait_for(qtbot, lambda: not window.isVisible())
    assert window.closing.finishing is None


def test_close_during_a_run_adds_the_cloud_note(qtbot: QtBot, tmp_path: Path) -> None:
    """A run that is not final adds the cloud note; Cancel keeps the window open."""
    active = [True]
    window = window_over(qtbot, GatedRunner(), tmp_path, run_active=lambda: active[0])
    assert not window.close()
    question = window.closing.question
    assert question is not None
    texts = [label.text() for label in question.findChildren(QLabel)]
    assert any(text.startswith("The cloud machine keeps working without the app") for text in texts)
    question.reject()
    assert window.isVisible()
    active[0] = False
    assert window.close()


class _Recorder:
    """The ports of "Quit now": records whether the log held the record when flushed, then the exit."""

    def __init__(self, log_file: Path) -> None:
        self._log_file = log_file
        self.order: list[str] = []

    def flush(self) -> None:
        flush_log_handlers()
        written = "before quit" in self._log_file.read_text(encoding="utf-8")
        self.order.append(f"flushed:{written}")

    def end(self, code: int) -> None:
        self.order.append(f"exit:{code}")

    def kill(self) -> None:
        self.order.append("killed")


def _stop_and_close(window: MainWindow) -> None:
    window.close()
    question = window.closing.question
    assert question is not None
    question.close_button.click()


def test_quit_now_flushes_the_log_then_exits(qtbot: QtBot, tmp_path: Path) -> None:
    """While a call ignores its flag, "Quit now" kills the tools, flushes and closes the log, then exits 0."""
    runner = GatedRunner((UPLOAD,), honour_cancel=False)
    log_file = tmp_path / "log" / "gui.log"
    recorder = _Recorder(log_file)
    window = window_over(qtbot, runner, tmp_path, QuitNow(recorder.flush, recorder.end, recorder.kill))
    with log_to_file(log_file):
        logging.getLogger("transcribe_ui.test").warning("before quit")
        window.context.executor.submit(upload(), lambda _response: None)
        wait_for(qtbot, lambda: runner.started(UPLOAD) == 1)
        _stop_and_close(window)
        finishing = window.closing.finishing
        assert finishing is not None
        finishing.quit_now_button.click()
    assert recorder.order == ["killed", "flushed:True", "exit:0"]
    runner.release()
    wait_for(qtbot, lambda: not window.isVisible())


def _first_line(holder: subprocess.Popen[str]) -> str:
    """Return the first stdout line of the holder, or "" if none came within the timeout."""
    lines: list[str] = []
    stream = holder.stdout
    assert stream is not None
    reader = threading.Thread(target=lambda: lines.append(stream.readline()), daemon=True)
    reader.start()
    reader.join(SUBPROCESS_TIMEOUT_S)
    return lines[0].strip() if lines else ""


def _hold_lock(root: Path) -> subprocess.Popen[str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, [str(TESTS_DIR), environment.get("PYTHONPATH")]))
    return subprocess.Popen(  # noqa: S603 -- runs this repo's test helper with the test interpreter
        [sys.executable, str(TESTS_DIR / "support" / "lock_holder.py"), str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=environment,
    )


def _notice_follows_the_page(window: MainWindow) -> None:
    window.pages.show_page(PAGE_SETTINGS)
    assert not window.pages.notice_shown()
    window.pages.show_page(PAGE_JOBS)
    assert window.pages.notice_shown()


def _busy_then_retry(qtbot: QtBot, tmp_path: Path, holder: subprocess.Popen[str]) -> None:
    ports = seeded_ports(tmp_path)
    with closing(ports.state):
        window = window_over(qtbot, Backend(ports), tmp_path)
        reloaded: list[object] = []
        window.context.signals.batches_changed.connect(reloaded.append)
        window.context.hooks.run()
        wait_for(qtbot, window.pages.notice_shown)
        _notice_follows_the_page(window)
        holder.communicate("\n", timeout=SUBPROCESS_TIMEOUT_S)
        window.pages.notice.retry_button.click()
        wait_for(qtbot, lambda: window.context.lock.state == "held")
        assert (window.pages.notice_shown(), reloaded) == (False, [()])
        window.close()


def test_app_busy_notice_from_a_second_process(qtbot: QtBot, tmp_path: Path) -> None:
    """Another process holds the writer lock: pages that need the database show the notice; Retry recovers."""
    holder = _hold_lock(tmp_path)
    with ExitStack() as cleanup:
        # The holder never outlives the test, whatever fails.
        cleanup.callback(holder.wait, SUBPROCESS_TIMEOUT_S)
        cleanup.callback(holder.kill)
        assert _first_line(holder) == "locked"
        _busy_then_retry(qtbot, tmp_path, holder)
