"""Races of `launch_worker` with the manifest step (threads; plan m5 M5.11, A21).

A manifest is published without an activity check, so one published while a launch is under
way is `ready` outside the run's scope; the run's lease, written before the pod request, does not
list it, so the run never processes it (the worker reads only the lease's batches).
"""

import threading

import httpx
import pytest

from support.batches import FIRST_BATCH_ID, SECOND_BATCH_ID
from support.launch import (
    LAUNCH,
    PODS,
    ok_as,
    plan_fingerprint,
    send,
    set_up_cloud,
)
from support.launch_seed import seed_audio_batch
from support.runs import READY, RunsWorld, batch_state, seed_ready
from support.worker_sim import leased_batches
from transcribe_ui.runs.responses import LaunchWorkerResponse

# Item of the second batch.
SECOND_ITEM = "01JDZZZZZZZZZZZZZZZZZZZZ31"
# Seconds a test thread waits for an event at most.
THREAD_TIMEOUT_S = 10
# How long the launch's request is paused while the other thread competes.
PAUSE_S = 0.2


class Launch:
    """`launch_worker` in a thread, paused once at the first request matching `method` and `path`."""

    def __init__(self, world: RunsWorld, method: str, path: str) -> None:
        """Prepare the launch on the world's confirmed plan."""
        self.world = world
        self.reached = threading.Event()
        self.launched: LaunchWorkerResponse | None = None
        self._target = (method, path)
        self._fingerprint = plan_fingerprint(world)
        self._thread = threading.Thread(target=self._run)

    def start(self) -> None:
        """Start the launch and wait until it reaches the paused request."""
        self.world.cloud.settings.on_request = self._pause
        self._thread.start()
        assert self.reached.wait(THREAD_TIMEOUT_S)

    def join(self) -> LaunchWorkerResponse:
        """Wait for the launch; return its response."""
        self._thread.join(THREAD_TIMEOUT_S)
        assert self.launched is not None
        return self.launched

    def _run(self) -> None:
        response = send(self.world, LAUNCH, confirm_fingerprint=self._fingerprint)
        self.launched = ok_as(response, LaunchWorkerResponse)

    def _pause(self, request: httpx.Request) -> None:
        if (request.method, request.url.path) != self._target or self.reached.is_set():
            return
        self.reached.set()
        threading.Event().wait(PAUSE_S)


@pytest.fixture
def two_batches(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up, the first batch published and a second one uploaded without its manifest step yet."""
    set_up_cloud(runs_world)
    seed_ready(runs_world)
    seed_audio_batch(runs_world, SECOND_BATCH_ID, SECOND_ITEM)
    return runs_world


def test_published_during_launch_not_processed(two_batches: RunsWorld) -> None:
    """A manifest step racing the launch's POST publishes, but the run's lease lists only the confirmed scope."""
    launch = Launch(two_batches, "POST", PODS)
    launch.start()
    two_batches.step(SECOND_BATCH_ID)
    launched = launch.join()
    run_id = launched.run.run_id
    assert (launched.run.covered_batch_ids, leased_batches(two_batches.store, run_id)) == (
        (FIRST_BATCH_ID,),
        (FIRST_BATCH_ID,),
    )
    assert (batch_state(two_batches), batch_state(two_batches, SECOND_BATCH_ID)) == (READY, READY)


def test_published_before_the_check_is_covered(two_batches: RunsWorld) -> None:
    """A manifest published before the launch reads its scope is in the scope and in the lease."""
    two_batches.step(SECOND_BATCH_ID)
    launch = Launch(two_batches, "GET", PODS)
    launch.start()
    launched = launch.join()
    covered = (FIRST_BATCH_ID, SECOND_BATCH_ID)
    lease = leased_batches(two_batches.store, launched.run.run_id)
    assert (launched.run.covered_batch_ids, lease) == (covered, covered)
