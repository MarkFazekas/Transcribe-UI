"""Channels of the GUI's executor: exclusive, latest wins, single flight (plan m6 §1 "Threading")."""

from collections.abc import Iterator
from types import MappingProxyType

import pytest
from pytestqt.qtbot import QtBot

from support.gated import DRAIN_MS, REFRESH, UPLOAD, GatedRunner, listing, refresh, uploads, wait_for
from transcribe_ui.api.classes import ErrorResponse, GetAppInfoResponse, Response
from transcribe_ui.api.constants import ERROR_CANCELLED
from transcribe_ui.gui.bridge import QtExecutor
from transcribe_ui.gui.constants import LONG_POOL, POLL_POOL, SHORT_POOL, CallState
from transcribe_ui.gui.executor import ExclusiveChannel, LatestChannel, SingleFlightChannel
from transcribe_ui.upload.classes import RunUploadRequest

# One thread per pool, so a held call keeps the next one queued.
SINGLE_THREADS = MappingProxyType({LONG_POOL: 1, SHORT_POOL: 1, POLL_POOL: 1})
KEY = "preview"
# Calls that reach the backend when a held call and the latest one run.
TWO_CALLS = 2


@pytest.fixture
def gated() -> GatedRunner:
    """Return a runner that holds every `run_upload` and `refresh_status` until released."""
    return GatedRunner((UPLOAD, REFRESH))


@pytest.fixture
def executor(gated: GatedRunner, qtbot: QtBot) -> Iterator[QtExecutor]:
    """Return a `QtExecutor` with one thread per pool; released and drained afterwards."""
    created = QtExecutor(gated, SINGLE_THREADS)
    yield created
    gated.release()
    wait_for(qtbot, lambda: created.pending() == 0)
    assert created.wait_for_threads(DRAIN_MS)


def _batches(gated: GatedRunner) -> list[str]:
    return [request.batch_id for request in gated.requests if isinstance(request, RunUploadRequest)]


def _state(channel: SingleFlightChannel) -> CallState | None:
    # A function, so a checked state does not narrow the next read of the property.
    return channel.state


def _kinds(answers: list[Response]) -> list[type]:
    return [type(answer) for answer in answers]


def test_exclusive_refuses_a_second_send(executor: QtExecutor, gated: GatedRunner, qtbot: QtBot) -> None:
    """One call per key: refused while queued or running, other keys go on, the key is free once done."""
    channel = ExclusiveChannel(executor)
    first, second = uploads(TWO_CALLS)
    responses: list[Response] = []
    sent = [channel.send("a", first, responses.append), channel.send("a", first, responses.append)]
    sent.append(channel.send("b", second, responses.append))
    assert ([call is None for call in sent], channel.busy("a")) == ([False, True, False], True)
    gated.release()
    wait_for(qtbot, lambda: len(responses) == TWO_CALLS)
    assert not channel.busy("a")
    assert channel.send("a", first, responses.append) is not None


def test_latest_takes_queued_call_off_pool(executor: QtExecutor, gated: GatedRunner, qtbot: QtBot) -> None:
    """A new send cancels the queued previous call (it never runs) and only the latest answer arrives."""
    blocker, older, newer = uploads(3)
    executor.submit(blocker, lambda _response: None)
    wait_for(qtbot, lambda: gated.started(UPLOAD) == 1)
    channel = LatestChannel(executor)
    answers: list[Response] = []
    first = channel.send(KEY, older, answers.append)
    channel.send(KEY, newer, answers.append)
    assert first.state == "done"
    gated.release()
    wait_for(qtbot, lambda: executor.pending() == 0)
    assert _batches(gated) == [blocker.batch_id, newer.batch_id]
    assert _kinds(answers) == [GetAppInfoResponse]


def test_latest_drops_stale_running_answer(executor: QtExecutor, gated: GatedRunner, qtbot: QtBot) -> None:
    """A running previous call gets its flag set; its `cancelled` answer is dropped as stale."""
    older, newer = uploads(TWO_CALLS)
    channel = LatestChannel(executor)
    answers: list[Response] = []
    channel.send(KEY, older, answers.append)
    wait_for(qtbot, lambda: gated.started(UPLOAD) == 1)
    channel.send(KEY, newer, answers.append)
    wait_for(qtbot, lambda: gated.started(UPLOAD) == TWO_CALLS)
    gated.release()
    wait_for(qtbot, lambda: executor.pending() == 0)
    assert (gated.cancelled, _kinds(answers)) == ([UPLOAD], [GetAppInfoResponse])


def test_single_flight_waits_on_a_queued_tick(executor: QtExecutor, gated: GatedRunner, qtbot: QtBot) -> None:
    """A tick queued behind a start-up check shows as waiting; no second tick is sent meanwhile."""
    executor.submit(refresh(), lambda _response: None)
    wait_for(qtbot, lambda: gated.started(REFRESH) == 1)
    channel = SingleFlightChannel(executor)
    ticks: list[Response] = []
    assert channel.send(refresh(), ticks.append) is not None
    assert _state(channel) == "queued"
    assert channel.send(refresh(), ticks.append) is None
    gated.release()
    wait_for(qtbot, lambda: bool(ticks))
    assert (_state(channel), gated.started(REFRESH)) == (None, TWO_CALLS)


def test_single_flight_shows_a_running_tick(executor: QtExecutor, gated: GatedRunner, qtbot: QtBot) -> None:
    """A running tick shows as refreshing (its start is announced) and blocks the next one."""
    channel = SingleFlightChannel(executor)
    started: list[bool] = []
    ticks: list[Response] = []
    channel.send(refresh(), ticks.append, lambda: started.append(True))
    wait_for(qtbot, lambda: bool(started))
    assert _state(channel) == "running"
    assert channel.send(refresh(), ticks.append) is None
    gated.release()
    wait_for(qtbot, lambda: bool(ticks))
    assert _state(channel) is None


def test_cancelled_queued_tick_frees_channel(executor: QtExecutor, gated: GatedRunner, qtbot: QtBot) -> None:
    """A starved tick that is cancelled (taken off its pool) frees the channel for the next tick."""
    executor.submit(refresh(), lambda _response: None)
    wait_for(qtbot, lambda: gated.started(REFRESH) == 1)
    channel = SingleFlightChannel(executor)
    ticks: list[Response] = []
    channel.send(refresh(), ticks.append)
    channel.cancel()
    assert _state(channel) is None
    assert channel.send(refresh(), ticks.append) is not None
    gated.release()
    wait_for(qtbot, lambda: len(ticks) == TWO_CALLS)
    first = ticks[0]
    assert isinstance(first, ErrorResponse)
    assert first.error.code == ERROR_CANCELLED
    assert (_kinds(ticks[1:]), gated.started(REFRESH)) == ([GetAppInfoResponse], TWO_CALLS)


def test_channel_waits_for_delivery(executor: QtExecutor, qtbot: QtBot) -> None:
    """A call the backend answered stays busy until its answer reached the sender on the GUI thread."""
    channel = ExclusiveChannel(executor)
    responses: list[Response] = []
    channel.send("list", listing(), responses.append)
    # Wait for the pool thread without running the event loop: answered, not delivered yet.
    assert executor.wait_for_threads(DRAIN_MS)
    assert (channel.busy("list"), responses) == (True, [])
    wait_for(qtbot, lambda: bool(responses))
    assert not channel.busy("list")
