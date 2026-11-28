"""The Cloud area's view-models over a seeded launch world, with a manual timer, clock and executor (plan m6 M6.6).

The world is one launch parity leg (`LaunchLegs` with the held batch and another computer's
manifest, on the fixed clock), so the requests the view-models build replay alike through the
parity runner. Nothing sleeps: the poll's timer fires when the test says so, and the manual
executor answers a call only when the test lets it.
"""

import json
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from support.batches import build_batch_tree
from support.credentials import moto_bucket
from support.launch_legs import LaunchLegs, seed_held_leg
from support.runpod import Cloud
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import Request, Response
from transcribe_ui.api.ports import CancelFlag, CancelToken, Ports, ProgressSink
from transcribe_ui.gui.cloud.area import CloudModels, build_models
from transcribe_ui.gui.cloud.ports import Tick
from transcribe_ui.gui.constants import CALL_DONE, CALL_QUEUED, CALL_RUNNING, CallState
from transcribe_ui.gui.executor import CallHandle, DoneCallback, ProgressCallback, StartedCallback, SyncExecutor
from transcribe_ui.gui.feedback import ConfirmationView, ErrorView, Fix
from transcribe_ui.storage.r2 import R2ObjectStore

# A signal the Cloud area emitted: its name and its arguments.
type Emitted = tuple[str, tuple[object, ...]]


@dataclass
class ManualTimer:
    """A timer that ticks only when the test fires it."""

    interval_ms: int
    tick: Tick
    active: bool = False
    # How often it was (re)started.
    starts: int = 0

    def start(self) -> None:
        """Start (or restart) the timer."""
        self.active = True
        self.starts += 1

    def stop(self) -> None:
        """Stop the timer."""
        self.active = False

    def fire(self) -> None:
        """Let one interval pass (only a running timer ticks)."""
        if self.active:
            self.tick()


@dataclass
class ManualTimers:
    """Builds manual timers and keeps them, so a test can fire them."""

    built: list[ManualTimer] = field(default_factory=list)

    def __call__(self, interval_ms: int, tick: Tick) -> ManualTimer:
        """Return a new manual timer."""
        timer = ManualTimer(interval_ms, tick)
        self.built.append(timer)
        return timer

    @property
    def poll(self) -> ManualTimer:
        """Return the poll's timer (the only one the view-models build)."""
        assert len(self.built) == 1, self.built
        return self.built[0]


@dataclass
class RecordingOutlet:
    """Records what the Cloud area sends to the window; confirms at once when `accept`."""

    accept: bool = True
    emitted: list[Emitted] = field(default_factory=list)
    asked: list[ConfirmationView] = field(default_factory=list)
    errors: list[ErrorView] = field(default_factory=list)
    fixes: list[Fix] = field(default_factory=list)
    alerts: int = 0

    def emit(self, signal_name: str, *arguments: object) -> None:
        """Record a signal."""
        self.emitted.append((signal_name, arguments))

    def confirm(self, view: ConfirmationView, on_confirm: Callable[[], None]) -> None:
        """Record the question; confirm at once when `accept`."""
        self.asked.append(view)
        if self.accept:
            on_confirm()

    def show_error(self, view: ErrorView, _retry: object) -> None:
        """Record the error."""
        self.errors.append(view)

    def fix(self, fix: Fix) -> None:
        """Record the fix."""
        self.fixes.append(fix)

    def alert(self) -> None:
        """Count the task-bar alert."""
        self.alerts += 1

    def signals(self, signal_name: str) -> list[tuple[object, ...]]:
        """Return the arguments of each emission of a signal."""
        return [arguments for name, arguments in self.emitted if name == signal_name]


@dataclass
class RecordingRunner:
    """Runs requests on a backend and records them."""

    backend: Backend
    requests: list[Request] = field(default_factory=list)

    def execute(self, request: Request, progress: ProgressSink | None, cancel: CancelToken | None) -> Response:
        """Record the request, then run it."""
        self.requests.append(request)
        return self.backend.execute(request, progress, cancel)

    def actions(self) -> list[str]:
        """Return the actions sent so far."""
        return [request.action for request in self.requests]


@dataclass
class _HeldCall:
    request: Request
    on_done: DoneCallback
    on_started: StartedCallback | None
    state: CallState = CALL_QUEUED

    def cancel(self) -> None:
        """Nothing runs before the test lets it."""


@dataclass
class _CallView:
    held: _HeldCall

    @property
    def state(self) -> CallState:
        return self.held.state

    def cancel(self) -> None:
        self.held.cancel()


@dataclass
class ManualExecutor:
    """Holds every call until the test starts and answers it (from the backend, or with a given response)."""

    runner: RecordingRunner
    held: list[_HeldCall] = field(default_factory=list)

    def submit(
        self,
        request: Request,
        on_done: DoneCallback,
        on_progress: ProgressCallback | None = None,  # noqa: ARG002 -- no request here reports progress
        on_started: StartedCallback | None = None,
    ) -> CallHandle:
        """Hold the call (queued)."""
        held = _HeldCall(request, on_done, on_started)
        self.held.append(held)
        return _CallView(held)

    def pending(self, action: str) -> list[_HeldCall]:
        """Return the held calls of an action that are not answered yet, oldest first."""
        return [held for held in self.held if held.request.action == action and held.state != CALL_DONE]

    def start(self, action: str) -> None:
        """Let the oldest held call of an action start running."""
        held = self.pending(action)[0]
        held.state = CALL_RUNNING
        if held.on_started is not None:
            held.on_started()

    def answer(self, action: str, response: Response | None = None) -> Response:
        """Answer the oldest held call of an action: with `response`, else by running it on the backend."""
        held = self.pending(action)[0]
        if response is None:
            response = self.runner.execute(held.request, None, CancelFlag())
        held.state = CALL_DONE
        held.on_done(response)
        return response

    def answer_all(self) -> None:
        """Answer every held call, in order, until none is left (answers may send new calls)."""
        open_calls = self._open()
        while open_calls:
            self.answer(open_calls[0].request.action)
            open_calls = self._open()

    def _open(self) -> list[_HeldCall]:
        return [held for held in self.held if held.state != CALL_DONE]


@dataclass
class CloudWorld:
    """One launch leg: its backend, its fake Runpod API and its bucket."""

    ports: Ports
    cloud: Cloud
    store: R2ObjectStore
    root: Path

    @property
    def backend(self) -> Backend:
        """Return a backend over the leg's ports."""
        return Backend(self.ports)

    def now(self) -> datetime:
        """Return the test time."""
        return self.cloud.clock.now()


@contextmanager
def cloud_world(tmp_path: Path) -> Iterator[CloudWorld]:
    """Return a launch leg like the launch parity's (held batch, another computer's job); closed afterwards."""
    root = tmp_path / "gui"
    root.mkdir()
    build_batch_tree(root)
    legs = LaunchLegs(seed=seed_held_leg)
    with moto_bucket():
        ports = legs(root)
        with closing(ports.state):
            yield CloudWorld(ports, legs.clouds[0], legs.stores[0], root)


@dataclass
class CloudArea:
    """The Cloud area's view-models over a world, with their outlet, timers and recorded requests."""

    models: CloudModels
    outlet: RecordingOutlet
    timers: ManualTimers
    runner: RecordingRunner


def cloud_area(backend: Backend, *, manual: bool = False) -> tuple[CloudArea, ManualExecutor | None]:
    """Return the view-models on `SyncExecutor` (or, with `manual`, on a `ManualExecutor` the test drives)."""
    runner = RecordingRunner(backend)
    outlet = RecordingOutlet()
    timers = ManualTimers()
    executor = ManualExecutor(runner) if manual else SyncExecutor(runner)
    models = build_models(executor, outlet, timers)
    area = CloudArea(models, outlet, timers, runner)
    return area, executor if isinstance(executor, ManualExecutor) else None


def documents(requests: Sequence[Request]) -> str:
    """Return requests as the text of a request golden file."""
    found = [request.model_dump(mode="json") for request in requests]
    return f"{json.dumps(found, indent=2, ensure_ascii=False)}\n"
