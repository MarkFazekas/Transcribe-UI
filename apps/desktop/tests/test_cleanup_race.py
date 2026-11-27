"""`cleanup_cloud` racing `launch_worker` (threads; plan m5 M5.14).

Both hold the runs lock for their whole check-and-act sequence, so whichever takes it first wins:
a cleanup that waits for a launch sees the new run and is refused, and a launch that waits for a
cleanup plans again and finds nothing left to transcribe. A worker never gets a batch whose data
is being deleted.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
import pytest

from support.batch_backend import error_code
from support.batches import FIRST_BATCH_ID
from support.cleanup import confirmed, flags, keys_under, patch_store, send
from support.interrupt import TripStore, Tripwire
from support.launch import LAUNCH, PODS, document, plan_fingerprint, posts, seed_ready, set_up_cloud
from support.runs import RunsWorld
from transcribe_contracts.layout import manifest_key
from transcribe_ui.api.classes import Response

# Seconds a test thread waits for an event at most.
THREAD_TIMEOUT_S = 10
# How long the paused request holds the runs lock while the other one waits (below the test wait of 0.5 s).
PAUSE_S = 0.2


@dataclass(frozen=True)
class _PausedAtPost:
    """A fake-API hook that pauses the first pod request once."""

    reached: threading.Event

    def __call__(self, request: httpx.Request) -> None:
        """Pause the first `POST /v2/pods`."""
        if (request.method, request.url.path) != ("POST", PODS) or self.reached.is_set():
            return
        self.reached.set()
        threading.Event().wait(PAUSE_S)


@dataclass
class _InThread:
    """A request sent from another thread."""

    world: RunsWorld
    request: str
    response: Response | None = None
    _thread: threading.Thread | None = field(default=None, repr=False)

    def start(self) -> None:
        """Send the request in a new thread."""
        self._thread = threading.Thread(target=self._run)
        self._thread.start()

    def join(self) -> Response:
        """Wait for the request; return its response."""
        assert self._thread is not None
        self._thread.join(THREAD_TIMEOUT_S)
        assert self.response is not None
        return self.response

    def _run(self) -> None:
        self.response = send(self.world, self.request)


@pytest.fixture
def ready(runs_world: RunsWorld) -> RunsWorld:
    """The cloud set up and the batch published (`ready`, its jobs pending)."""
    set_up_cloud(runs_world)
    seed_ready(runs_world)
    return runs_world


def test_cleanup_waiting_for_a_launch_sees_its_run(ready: RunsWorld) -> None:
    """The launch holds the runs lock at its pod request; the cleanup then finds the run: `run_active`."""
    reached = threading.Event()
    ready.cloud.settings.on_request = _PausedAtPost(reached)
    launch = _InThread(ready, document(LAUNCH, confirm_fingerprint=plan_fingerprint(ready)))
    launch.start()
    assert reached.wait(THREAD_TIMEOUT_S)
    cleanup = send(ready, confirmed("everything"))
    launched = launch.join()
    if error_code(cleanup) == "runs_busy":
        cleanup = send(ready, confirmed("everything"))
    assert (launched.status, error_code(cleanup)) == ("ok", "run_active")
    assert flags(ready) == ("ready", False)
    assert manifest_key(FIRST_BATCH_ID) in keys_under(ready.store)


@dataclass
class _Pause(Tripwire):
    """Pauses the first object-store call once, holding the cleanup's runs lock meanwhile."""

    reached: threading.Event = field(default_factory=threading.Event)

    def around[ResultT](self, call: Callable[[], ResultT]) -> ResultT:
        """Pause before the first call, then make it."""
        if not self.reached.is_set():
            self.reached.set()
            threading.Event().wait(PAUSE_S)
        return call()


def test_launch_waiting_for_a_cleanup_finds_nothing(ready: RunsWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """The cleanup holds the runs lock; the launch then plans again: the batch is gone, nothing starts."""
    fingerprint = plan_fingerprint(ready)
    pause = _Pause()
    patch_store(monkeypatch, "known", lambda store: TripStore(store, pause))
    cleanup = _InThread(ready, confirmed("everything"))
    cleanup.start()
    assert pause.reached.wait(THREAD_TIMEOUT_S)
    launched = send(ready, document(LAUNCH, confirm_fingerprint=fingerprint))
    assert cleanup.join().status == "ok"
    assert error_code(launched) in {"nothing_to_launch", "runs_busy"}
    again = send(ready, document(LAUNCH, confirm_fingerprint=fingerprint))
    assert (error_code(again), posts(ready)) == ("nothing_to_launch", 0)
    assert (flags(ready), keys_under(ready.store)) == (("cleaned", True), [])
