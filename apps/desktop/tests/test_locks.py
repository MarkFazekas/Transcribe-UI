"""Flow tests of the in-process locks (plan m5 M5.2): batch locks never wait, the runs lock waits bounded."""

import threading
import time
from functools import partial
from pathlib import Path

import pytest

from support.fakes import TEST_RUNS_LOCK_WAIT_S, seeded_ports
from transcribe_ui.api.backend import Backend, Route
from transcribe_ui.api.classes import ErrorResponse, GetAppInfoRequest, GetAppInfoResponse, Response
from transcribe_ui.api.errors import CancelledError
from transcribe_ui.api.info import get_app_info
from transcribe_ui.api.ports import CancelFlag, RequestContext
from transcribe_ui.state.errors import RunsBusyError
from transcribe_ui.state.locks import LockRegistry

GET_APP_INFO = GetAppInfoRequest(action="get_app_info")
BATCH_ID = "batch_20261127_120000"
THREAD_TIMEOUT_S = 10
SHORT_HOLD_S = 0.2


def _hold_batch(
    entered: threading.Event,
    release: threading.Event,
    request: GetAppInfoRequest,
    context: RequestContext,
) -> Response:
    with context.ports.state.locks.batch(BATCH_ID):
        entered.set()
        release.wait(THREAD_TIMEOUT_S)
        return get_app_info(request, context)


def test_batch_lock_refuses_a_concurrent_request(tmp_path: Path) -> None:
    """A request that finds its batch locked by another thread gets `batch_busy` at once."""
    entered = threading.Event()
    release = threading.Event()
    run = partial(_hold_batch, entered, release)
    backend = Backend(seeded_ports(tmp_path), {"get_app_info": Route(run=run, needs_database=False)})
    outcomes: list[Response] = []
    holder = threading.Thread(target=lambda: outcomes.append(backend.execute(GET_APP_INFO)))
    holder.start()
    assert entered.wait(THREAD_TIMEOUT_S)
    started = time.monotonic()
    refused = backend.execute(GET_APP_INFO)
    assert time.monotonic() - started < TEST_RUNS_LOCK_WAIT_S
    release.set()
    holder.join(THREAD_TIMEOUT_S)
    assert isinstance(refused, ErrorResponse)
    assert (refused.error.code, refused.error.details) == ("batch_busy", {"batch_id": BATCH_ID})
    assert [type(outcome) for outcome in (outcomes[0], backend.execute(GET_APP_INFO))] == [
        GetAppInfoResponse,
        GetAppInfoResponse,
    ]


def _hold_runs(locks: LockRegistry, entered: threading.Event, hold_s: float) -> None:
    with locks.runs():
        entered.set()
        time.sleep(hold_s)


def _holder(locks: LockRegistry, hold_s: float) -> threading.Thread:
    entered = threading.Event()
    thread = threading.Thread(target=_hold_runs, args=(locks, entered, hold_s))
    thread.start()
    assert entered.wait(THREAD_TIMEOUT_S)
    return thread


def test_runs_lock_waits_then_gives_runs_busy() -> None:
    """The runs lock is waited for at most the bounded wait, then the request gets `runs_busy`."""
    wait_s = 0.3
    locks = LockRegistry(runs_wait_s=wait_s)
    holder = _holder(locks, hold_s=wait_s * 4)
    started = time.monotonic()
    with pytest.raises(RunsBusyError), locks.runs():
        pytest.fail("the runs lock was taken while held")
    waited = time.monotonic() - started
    holder.join(THREAD_TIMEOUT_S)
    assert wait_s <= waited < wait_s + 2


def test_runs_lock_is_handed_over_within_the_wait() -> None:
    """A holder that finishes within the wait hands the runs lock over."""
    locks = LockRegistry(runs_wait_s=THREAD_TIMEOUT_S)
    holder = _holder(locks, hold_s=SHORT_HOLD_S)
    started = time.monotonic()
    with locks.runs():
        waited = time.monotonic() - started
    holder.join(THREAD_TIMEOUT_S)
    assert waited < THREAD_TIMEOUT_S / 2


def test_runs_lock_wait_stops_on_cancel() -> None:
    """Cancelling while the runs lock is waited for stops the wait with `cancelled`."""
    locks = LockRegistry(runs_wait_s=THREAD_TIMEOUT_S)
    holder = _holder(locks, hold_s=1)
    flag = CancelFlag()
    threading.Timer(SHORT_HOLD_S, flag.cancel).start()
    started = time.monotonic()
    with pytest.raises(CancelledError), locks.runs(flag.is_cancelled):
        pytest.fail("the runs lock was taken while held")
    assert time.monotonic() - started < THREAD_TIMEOUT_S / 2
    holder.join(THREAD_TIMEOUT_S)
