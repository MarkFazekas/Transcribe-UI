"""The GUI's request bridge: pools, queued-call cancellation and close (plan m6 M6.2)."""

import gc
import time
import weakref
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QThreadPool
from pytestqt.qtbot import QtBot

from support.gated import (
    DRAIN_MS,
    REFRESH,
    SCAN,
    UPLOAD,
    GatedRunner,
    listing,
    refresh,
    scan,
    stop,
    upload,
    uploads,
    wait_for,
)
from support.parity import RecordingProgress
from transcribe_ui.api.classes import (
    ErrorResponse,
    GetAppInfoRequest,
    Request,
    Response,
)
from transcribe_ui.api.constants import ERROR_CANCELLED
from transcribe_ui.api.routes import default_routes
from transcribe_ui.gui.bridge import QtExecutor, pool_of
from transcribe_ui.gui.constants import (
    ACTION_POOLS,
    LONG_POOL,
    LONG_POOL_THREADS,
    POLL_POOL,
    POOL_THREADS,
    SCAN_POOL,
    SHORT_POOL,
)
from transcribe_ui.gui.executor import CallHandle, PooledCall

# Timeout of the shared-deadline test, in milliseconds (three pools would take three times as long).
WAIT_ONCE_MS = 500
# Instant calls of the runnable stress test.
STRESS_CALLS = 2000
UPLOAD_LATER = "batch_20261127_130000"


# The responses one call delivered (exactly one once it completed).
type Answers = list[Response]
# What one submit of the mixed run collects.
type Collected = tuple[Answers, RecordingProgress]


@pytest.fixture
def gated() -> GatedRunner:
    """Return a runner that holds every `run_upload` and `refresh_status` until released."""
    return GatedRunner((UPLOAD, REFRESH))


@pytest.fixture
def executor(gated: GatedRunner, qtbot: QtBot) -> Iterator[QtExecutor]:
    """Return a `QtExecutor` over the gated runner; released and drained afterwards."""
    created = QtExecutor(gated)
    yield created
    gated.release()
    wait_for(qtbot, lambda: created.pending() == 0)
    assert created.wait_for_threads(DRAIN_MS)


def _collect(executor: QtExecutor, request: Request) -> tuple[CallHandle, Answers]:
    responses: Answers = []
    return executor.submit(request, responses.append), responses


def _only(responses: Answers) -> Response:
    assert len(responses) == 1
    return responses[0]


def _code(responses: Answers) -> str | None:
    response = _only(responses)
    return response.error.code if isinstance(response, ErrorResponse) else None


def _ignore(_response: Response) -> None:
    """Drop the answer."""


def _never_taken() -> bool:
    return False


def test_listed_actions_have_routes_and_pools() -> None:
    """The pool table names real actions only; Stop and lists run on the short pool, the poll on its own."""
    assert set(ACTION_POOLS) <= set(default_routes())
    assert {pool_of("cancel_worker"), pool_of("terminate_pod"), pool_of("list_batches")} == {SHORT_POOL}
    assert (pool_of(SCAN), pool_of(UPLOAD)) == (SCAN_POOL, LONG_POOL)
    assert pool_of(REFRESH) == POLL_POOL


def test_short_call_runs_beside_a_full_long_pool(
    executor: QtExecutor,
    gated: GatedRunner,
    qtbot: QtBot,
) -> None:
    """Stop is answered while every long-pool thread holds an upload and another upload waits."""
    held = [_collect(executor, request) for request in uploads(LONG_POOL_THREADS + 1)]
    wait_for(qtbot, lambda: gated.started(UPLOAD) == LONG_POOL_THREADS)
    _stop_call, stopped = _collect(executor, stop())
    _list_call, listed = _collect(executor, listing())
    wait_for(qtbot, lambda: bool(stopped) and bool(listed))
    assert not any(responses for _call, responses in held)
    assert held[-1][0].state == "queued"


def test_scan_runs_beside_a_full_long_pool(
    executor: QtExecutor,
    gated: GatedRunner,
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    """A new folder scan is answered while every long-pool thread holds an upload and another waits."""
    held = [_collect(executor, request) for request in uploads(LONG_POOL_THREADS + 1)]
    wait_for(qtbot, lambda: gated.started(UPLOAD) == LONG_POOL_THREADS)
    _scan_call, scanned = _collect(executor, scan(tmp_path))
    wait_for(qtbot, lambda: bool(scanned))
    assert not any(responses for _call, responses in held)


def test_cancelled_queued_call_leaves_its_pool(
    executor: QtExecutor,
    gated: GatedRunner,
    qtbot: QtBot,
) -> None:
    """A queued call cancelled is done at once, answers `cancelled` and never reaches the backend."""
    for request in uploads(LONG_POOL_THREADS):
        _collect(executor, request)
    wait_for(qtbot, lambda: gated.started(UPLOAD) == LONG_POOL_THREADS)
    queued, responses = _collect(executor, upload(UPLOAD_LATER))
    queued.cancel()
    assert queued.state == "done"
    wait_for(qtbot, lambda: bool(responses))
    assert _code(responses) == ERROR_CANCELLED
    gated.release()
    wait_for(qtbot, lambda: executor.pending() == 0)
    assert gated.started(UPLOAD) == LONG_POOL_THREADS


def test_call_cancelled_after_dequeuing_never_runs() -> None:
    """Cancelled between leaving its queue and starting: `begin` completes it as cancelled, no execute."""
    delivered: Answers = []
    call = PooledCall(upload(), _never_taken, lambda _call, response: delivered.append(response))
    call.cancel()
    assert (call.state, delivered) == ("queued", [])
    assert not call.begin()
    assert (call.state, _code(delivered)) == ("done", ERROR_CANCELLED)
    call.abandon()
    assert len(delivered) == 1


def test_close_clears_pools_and_stops_calls(
    executor: QtExecutor,
    gated: GatedRunner,
    qtbot: QtBot,
) -> None:
    """`shutdown`: queued calls never run, running calls see their flag, later sends never run."""
    calls = [_collect(executor, request) for request in uploads(LONG_POOL_THREADS + 2)]
    calls.extend(_collect(executor, refresh()) for _ in range(2))
    wait_for(qtbot, lambda: gated.started(UPLOAD) == LONG_POOL_THREADS and gated.started(REFRESH) == 1)
    executor.shutdown()
    calls.append(_collect(executor, listing()))
    wait_for(qtbot, lambda: executor.pending() == 0)
    assert {_code(responses) for _call, responses in calls} == {ERROR_CANCELLED}
    assert (gated.started(UPLOAD), gated.started(REFRESH), gated.started("list_batches")) == (LONG_POOL_THREADS, 1, 0)
    assert sorted(gated.cancelled) == sorted([REFRESH, *(UPLOAD for _ in range(LONG_POOL_THREADS))])


def _runnables() -> int:
    # The bridge's runnable class is private; count its live instances by name.
    return sum(1 for alive in gc.get_objects() if type(alive).__name__ == "_CallRunnable")


def _send_and_forget(executor: QtExecutor, gated: GatedRunner, qtbot: QtBot) -> list[weakref.ref[object]]:
    """Send uploads (one taken off its queue, one cancelled while running); return weak references only."""
    requests = uploads(LONG_POOL_THREADS + 1)
    calls = [executor.submit(request, _ignore) for request in requests]
    wait_for(qtbot, lambda: gated.started(UPLOAD) == LONG_POOL_THREADS)
    calls[-1].cancel()
    calls[0].cancel()
    return [weakref.ref(sent) for sent in (*requests, *calls)]


def test_completed_calls_are_freed(gated: GatedRunner, qtbot: QtBot) -> None:
    """Runnables, handles and requests of finished, cancelled and taken-off calls do not outlive them."""
    executor = QtExecutor(gated)
    gc.collect()
    before = _runnables()
    sent = _send_and_forget(executor, gated, qtbot)
    gated.release()
    wait_for(qtbot, lambda: executor.pending() == 0)
    assert executor.wait_for_threads(DRAIN_MS)
    # The gated runner records the requests it ran; only the bridge may not keep them.
    gated.requests.clear()
    gc.collect()
    assert [ref() for ref in sent] == [None for _ in sent]
    # An earlier test may still free a runnable late, never add one.
    assert _runnables() <= before


def test_thread_wait_shares_one_deadline(qtbot: QtBot, tmp_path: Path) -> None:
    """With a stuck call on every pool, the wait gives up after one timeout, not one per pool."""
    stuck = GatedRunner((SCAN, UPLOAD, REFRESH, "cancel_worker"), honour_cancel=False)
    executor = QtExecutor(stuck)
    for request in (scan(tmp_path), upload(), refresh(), stop()):
        executor.submit(request, _ignore)
    wait_for(qtbot, lambda: len(stuck.calls) == len(POOL_THREADS))
    started = time.monotonic()
    assert not executor.wait_for_threads(WAIT_ONCE_MS)
    elapsed_ms = (time.monotonic() - started) * 1000
    stuck.release()
    wait_for(qtbot, lambda: executor.pending() == 0)
    assert executor.wait_for_threads(DRAIN_MS)
    assert elapsed_ms < WAIT_ONCE_MS * 2


def test_many_instant_calls_free_their_runnables(qtbot: QtBot) -> None:
    """Calls that end at once (racing `start` against the pool's auto-delete) leave no runnable behind."""
    executor = QtExecutor(GatedRunner())
    gc.collect()
    before = _runnables()
    answers: list[Response] = []
    for _ in range(STRESS_CALLS):
        executor.submit(GetAppInfoRequest(action="get_app_info"), answers.append)
    wait_for(qtbot, lambda: len(answers) == STRESS_CALLS)
    assert executor.wait_for_threads(DRAIN_MS)
    gc.collect()
    # An earlier test may still free a runnable late, never add one.
    assert _runnables() <= before


def _refuse_start(_pool: QThreadPool, _runnable: object) -> None:
    message = "the pool refused the runnable"
    raise RuntimeError(message)


def test_a_failed_start_leaves_no_call_behind(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """`start` raising: the error reaches the sender, the call is forgotten, the executor goes on."""
    executor = QtExecutor(GatedRunner())
    monkeypatch.setattr(QThreadPool, "start", _refuse_start)
    with pytest.raises(RuntimeError, match="refused"):
        executor.submit(listing(), _ignore)
    monkeypatch.undo()
    assert executor.pending() == 0
    answers: list[Response] = []
    executor.submit(listing(), answers.append)
    wait_for(qtbot, lambda: bool(answers))
    assert executor.wait_for_threads(DRAIN_MS)
