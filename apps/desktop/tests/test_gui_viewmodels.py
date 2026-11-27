"""Qt-free parts of the GUI: feedback views, confirmations, the activity model, start-up hooks (plan m6 M6.2)."""

import subprocess
import sys
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest

from support.fakes import seeded_ports
from support.gated import GatedRunner, upload
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import (
    Confirmation,
    ErrorBody,
    ErrorResponse,
    GetAppInfoRequest,
    NeedsConfirmation,
    Request,
    Response,
)
from transcribe_ui.api.ports import CancelToken, ProgressSink
from transcribe_ui.cleanup.classes import CleanupCloudRequest
from transcribe_ui.gui.activity import Activity
from transcribe_ui.gui.constants import ACTIVITY_STATE_TEXTS, FIX_LABELS
from transcribe_ui.gui.executor import LatestChannel, SyncExecutor, cancelled_response
from transcribe_ui.gui.feedback import Fix, confirmation_view, confirmed_request, error_view
from transcribe_ui.gui.signals import StartupHooks, StartupSlot
from transcribe_ui.gui.writer_lock import WriterLockGate

SUBPROCESS_TIMEOUT_S = 60
# Sends of the latest-wins channel test.
SENDS = 3
# Modules a view-model test imports; none of them may load Qt.
QT_FREE_MODULES = (
    "transcribe_ui.gui.activity",
    "transcribe_ui.gui.display",
    "transcribe_ui.gui.executor",
    "transcribe_ui.gui.feedback",
    "transcribe_ui.gui.main",
    "transcribe_ui.gui.viewmodel",
    "transcribe_ui.gui.writer_lock",
)


def _error(code: str, details: dict[str, object] | None = None) -> ErrorResponse:
    return ErrorResponse(action="plan_launch", error=ErrorBody(code=code, message="It failed.", details=details or {}))


def test_view_model_modules_do_not_load_qt() -> None:
    """The view-model modules and the GUI entry module import no Qt (tests run them without a display)."""
    lines = ["import sys", *(f"import {name}" for name in QT_FREE_MODULES)]
    lines.append("assert not [name for name in sys.modules if name.startswith(('PySide6', 'shiboken6'))]")
    script = "\n".join(lines)
    completed = subprocess.run(  # noqa: S603 -- the test interpreter on a fixed script
        [sys.executable, "-c", script],
        capture_output=True,
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_error_view_offers_known_fixes() -> None:
    """Known codes get their fix (with the batch of `cleanup_pending`), others the generic layout."""
    pending = error_view(_error("cleanup_pending", {"batch_ids": ["batch_20261127_120000"]}))
    assert pending.fix == Fix("open_batch", FIX_LABELS["open_batch"], "batch_20261127_120000")
    missing = error_view(_error("credentials_missing"))
    assert missing.fix == Fix("accounts", FIX_LABELS["accounts"])
    unknown = error_view(_error("a_code_from_the_future", {"why": "new"}))
    assert (unknown.fix, unknown.see_log, unknown.details.rows) == (None, False, (("why", "new"),))
    assert error_view(_error("internal_error")).see_log


def test_details_render_lists_of_objects_as_tables() -> None:
    """A detail listing objects becomes a table with every key as a column; scalars become rows."""
    targets = [{"path": "a.srt", "size": 1}, {"path": "b.srt", "kept": True}]
    view = error_view(_error("overwrite", {"count": 2, "targets": targets, "names": ["x", "y"], "none": None}))
    assert view.details.rows == (("count", "2"), ("names", "x\ny"), ("none", ""))
    assert len(view.details.tables) == 1
    table = view.details.tables[0]
    assert (table.title, table.columns) == ("targets", ("path", "size", "kept"))
    assert table.rows == (("a.srt", "1", ""), ("b.srt", "", "yes"))


def test_confirming_sets_each_field() -> None:
    """Accepting a confirmation re-sends the same request with every listed field set."""
    request = CleanupCloudRequest(action="cleanup_cloud", batch_id="batch_20261127_120000", scope="everything")
    asked = NeedsConfirmation(
        action="cleanup_cloud",
        confirmations=(
            Confirmation(field="confirm", message="Delete?"),
            Confirmation(field="confirm_not_downloaded", message="Not downloaded yet.", details={"jobs": 3}),
        ),
    )
    view = confirmation_view(asked, request)
    assert [shown.message for shown in view.confirmations] == ["Delete?", "Not downloaded yet."]
    assert view.problem is None
    assert confirmed_request(request, asked) == request.model_copy(
        update={"confirm": True, "confirm_not_downloaded": True},
    )


def test_a_field_the_dialog_cannot_set_is_refused() -> None:
    """A confirmation field that is no boolean of the request disables Confirm with a reason."""
    request = GetAppInfoRequest(action="get_app_info")
    asked = NeedsConfirmation(
        action="get_app_info",
        confirmations=(Confirmation(field="confirm_fingerprint", message="Start?"),),
    )
    view = confirmation_view(asked, request)
    assert view.problem is not None
    assert "confirm_fingerprint" in view.problem
    with pytest.raises(ValueError, match="confirm_fingerprint"):
        confirmed_request(request, asked)


class _CancelledFirst:
    """Answers its first call `cancelled` (as after Cancel), later ones with app info."""

    def __init__(self) -> None:
        self.sent: list[Request] = []

    def execute(self, request: Request, progress: ProgressSink | None, cancel: CancelToken | None) -> Response:
        self.sent.append(request)
        if len(self.sent) == 1:
            return cancelled_response(request)
        return GatedRunner().execute(request, progress, cancel)


def test_activity_shows_a_finished_call() -> None:
    """A finished call is shown finished, without Resume."""
    finished = Activity(SyncExecutor(GatedRunner())).start(upload(), "Upload")
    assert finished is not None
    assert (finished.state, finished.status, finished.can_resume) == ("finished", "Finished", False)


def test_activity_resumes_a_stopped_call() -> None:
    """A cancelled call is shown stopped (neutral); Resume re-sends it; Dismiss removes it."""
    runner = _CancelledFirst()
    activity = Activity(SyncExecutor(runner))
    stopped = activity.start(upload(), "Upload")
    assert stopped is not None
    assert (stopped.state, stopped.status, stopped.can_resume) == ("stopped", ACTIVITY_STATE_TEXTS["stopped"], True)
    assert activity.resume(stopped)
    assert (stopped.state, len(runner.sent)) == ("finished", 2)
    activity.dismiss(stopped)
    assert not activity.entries


def test_latest_channel_on_sync_executor() -> None:
    """On `SyncExecutor` each send completes before the next, so every answer is the latest."""
    executor = SyncExecutor(GatedRunner())
    answers: list[Response] = []
    channel = LatestChannel(executor)
    for _ in range(SENDS):
        channel.send("key", GetAppInfoRequest(action="get_app_info"), answers.append)
    assert len(answers) == len(executor.sent) == SENDS


class _Step:
    """A start-up step that records its run and proceeds at once, twice, or only when told."""

    def __init__(self, name: str, ran: list[str], proceeds: int) -> None:
        self._name = name
        self._ran = ran
        self._proceeds = proceeds
        self.held: list[Callable[[], None]] = []

    def __call__(self, proceed: Callable[[], None]) -> None:
        self._ran.append(self._name)
        self.held.append(proceed)
        for _ in range(self._proceeds):
            proceed()


def test_startup_hooks_run_in_order() -> None:
    """Steps run by slot, then registration order; each starts only after the one before proceeded."""
    hooks = StartupHooks()
    ran: list[str] = []
    settings = _Step("settings", ran, proceeds=0)
    hooks.register(StartupSlot.REFRESH, _Step("refresh", ran, proceeds=1))
    hooks.register(StartupSlot.SETTINGS, settings)
    # A second `proceed` call does nothing.
    hooks.register(StartupSlot.DATABASE, _Step("database", ran, proceeds=2))
    finished: list[bool] = []
    hooks.run(lambda: finished.append(True))
    assert (ran, finished) == (["settings"], [])
    settings.held[0]()
    assert (ran, finished) == (["settings", "database", "refresh"], [True])


def test_writer_lock_probe_takes_the_lock(tmp_path: Path) -> None:
    """The start-up probe is a database request: on a free database the gate reads `held`."""
    ports = seeded_ports(tmp_path)
    with closing(ports.state):
        executor = SyncExecutor(Backend(ports))
        gate = WriterLockGate(executor)
        proceeded: list[bool] = []
        gate.probe(lambda: proceeded.append(True))
    assert (gate.state, proceeded) == ("held", [True])
    assert [request.action for request in executor.sent] == ["list_batches"]
