"""Races of `launch_worker` with the manifest step and `publish_held_manifests` (threads; plan m5 M5.11).

The launch holds the runs lock from its activity check to its pod request, so a manifest
published concurrently is either in the scope the launch confirmed or held back until the run
ends: never a `ready` batch outside the run's scope.
"""

import threading
from collections.abc import Callable

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
from support.runs import HELD, READY, RunsWorld, batch_state, seed_uploaded
from transcribe_ui.runs.responses import LaunchWorkerResponse
from transcribe_ui.state.errors import RunsBusyError

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


def _while_launching[ResultT](launch: Launch, compete: Callable[[], ResultT]) -> ResultT:
    """Run `compete` while the launch is paused; after `runs_busy`, again once the launch is done."""
    launch.start()
    try:
        return compete()
    except RunsBusyError:
        launch.join()
    return compete()


def _ready_outside_scope(world: RunsWorld, launched: LaunchWorkerResponse) -> list[str]:
    covered = set(launched.run.covered_batch_ids)
    states = {batch_id: batch_state(world, batch_id) for batch_id in (FIRST_BATCH_ID, SECOND_BATCH_ID)}
    return [batch_id for batch_id, state in states.items() if state[0] == "ready" and batch_id not in covered]


@pytest.fixture
def two_batches(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up, the first batch held and a second one uploaded without its manifest step yet."""
    set_up_cloud(runs_world)
    seed_uploaded(runs_world, held=True)
    seed_audio_batch(runs_world, SECOND_BATCH_ID, SECOND_ITEM)
    return runs_world


def test_manifest_step_during_the_pod_request_is_held(two_batches: RunsWorld) -> None:
    """A manifest step racing the launch's POST waits for the runs lock, then sees the run: held."""
    launch = Launch(two_batches, "POST", PODS)
    step = _while_launching(launch, lambda: two_batches.step(SECOND_BATCH_ID))
    launched = launch.join()
    assert (launched.run.covered_batch_ids, step.outcome) == ((FIRST_BATCH_ID,), "held")
    assert (batch_state(two_batches), batch_state(two_batches, SECOND_BATCH_ID)) == (READY, HELD)
    assert not _ready_outside_scope(two_batches, launched)


def test_publish_during_activity_check_adds_nothing(two_batches: RunsWorld) -> None:
    """`publish_held_manifests` racing the launch's activity check: the launch publishes its own scope."""
    assert two_batches.step(SECOND_BATCH_ID).outcome == "published"
    launch = Launch(two_batches, "GET", PODS)
    published = _while_launching(launch, two_batches.publish_held)
    launched = launch.join()
    assert (launched.published, launched.run.covered_batch_ids) == (
        (FIRST_BATCH_ID,),
        (FIRST_BATCH_ID, SECOND_BATCH_ID),
    )
    assert (published.published, _ready_outside_scope(two_batches, launched)) == ((), [])
