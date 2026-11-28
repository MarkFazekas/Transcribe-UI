"""The download dialog's view-model over the seeded download batch, with recording runners (plan m6 M6.9)."""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from support.batches import FIRST_BATCH_ID
from support.jobs import RecordingOutlet
from support.parity import Leg
from transcribe_ui.api.classes import ProgressEvent, Request, Response
from transcribe_ui.api.ports import CancelFlag, CancelToken, ProgressSink
from transcribe_ui.gui.activity import Activity
from transcribe_ui.gui.constants import CallState
from transcribe_ui.gui.download.state import DialogData, dialog_data
from transcribe_ui.gui.download.viewmodel import DownloadModel
from transcribe_ui.gui.executor import RequestRunner, SyncExecutor, cancelled_response


@dataclass
class CancelAfter:
    """Progress sink that passes events on and sets the cancel flag after `jobs` download events."""

    sink: ProgressSink | None
    flag: CancelFlag
    jobs: int

    def emit(self, event: ProgressEvent) -> None:
        """Pass the event on; cancel once enough jobs are done."""
        if self.sink is not None:
            self.sink.emit(event)
        if event.completed >= self.jobs:
            self.flag.cancel()


@dataclass
class Recording:
    """Runs requests on a runner and records them; can cancel the first download after some jobs (Cancel)."""

    runner: RequestRunner
    # Cancel the first `run_download` after this many jobs (None: never).
    cancel_after: int | None = None
    requests: list[Request] = field(default_factory=list)

    def execute(self, request: Request, progress: ProgressSink | None, cancel: CancelToken | None) -> Response:
        """Record the request, then run it (the first download cancelled when asked)."""
        self.requests.append(request)
        if request.action != "run_download" or self.cancel_after is None:
            return self.runner.execute(request, progress, cancel)
        flag = CancelFlag()
        sink = CancelAfter(progress, flag, self.cancel_after)
        self.cancel_after = None
        return self.runner.execute(request, sink, flag)


@dataclass
class ManualDeferral:
    """The plan delay of the tests: a pending call runs only when the test fires it."""

    pending: Callable[[], None] | None = None

    def call_later(self, callback: Callable[[], None]) -> None:
        """Keep the call (replacing a pending one)."""
        self.pending = callback

    def cancel(self) -> None:
        """Drop the pending call."""
        self.pending = None

    def fire(self) -> None:
        """Run the pending call, as the delay ending would."""
        callback = self.pending
        self.pending = None
        assert callback is not None, "no call is pending"
        callback()


@dataclass
class DialogOutlet(RecordingOutlet):
    """Records what the download dialog tells the window, also how often it asked to be shown again."""

    shown_again: int = 0
    # Called with each emitted signal (a test re-dispatches `batches_changed` to the model).
    on_emit: Callable[[str, tuple[object, ...]], None] | None = None

    def show_dialog(self) -> None:
        """Count the request to show the dialog."""
        self.shown_again += 1

    def emit(self, signal_name: str, *arguments: object) -> None:
        """Record a signal and pass it on."""
        super().emit(signal_name, *arguments)
        if self.on_emit is not None:
            self.on_emit(signal_name, arguments)


@dataclass
class DownloadArea:
    """The download dialog's view-model of the first batch on `SyncExecutor`, its activity and its outlet."""

    leg: Leg
    runner: Recording
    outlet: DialogOutlet
    activity: Activity
    model: DownloadModel
    deferral: ManualDeferral

    def shown(self) -> DialogData:
        """Return what the dialog shows now."""
        return dialog_data(self.model)

    def documents(self, requests: Sequence[Request] | None = None) -> str:
        """Return the requests (default: every request sent) as a golden file's text, paths as `<root>/...`."""
        sent = self.runner.requests if requests is None else requests
        context = self.leg.normaliser.context()
        documents = [request.model_dump(mode="json", context=context) for request in sent]
        return f"{json.dumps(documents, indent=2, ensure_ascii=False)}\n"

    def folder(self, name: str) -> str:
        """Return a folder below the leg's root."""
        return str(self.leg.root / name)


def download_area(leg: Leg, cancel_after: int | None = None) -> DownloadArea:
    """Return the first batch's download view-model over the leg's backend."""
    runner = Recording(leg.backend, cancel_after)
    executor = SyncExecutor(runner)
    activity = Activity(executor)
    outlet = DialogOutlet()
    deferral = ManualDeferral()
    model = DownloadModel(executor, activity, outlet, FIRST_BATCH_ID, deferral)
    return DownloadArea(leg, runner, outlet, activity, model, deferral)


@dataclass
class HeldCall:
    """A call `HeldExecutor` holds until the test releases it."""

    request: Request
    on_done: Callable[[Response], None]
    cancelled: bool = False
    answered: bool = False

    @property
    def state(self) -> CallState:
        """Return "queued" until released, then "done"."""
        return "done" if self.answered else "queued"

    def cancel(self) -> None:
        """Mark the call cancelled: it answers `cancelled` when released."""
        self.cancelled = True


@dataclass
class HeldExecutor:
    """Holds every call until `release`, so a test can change the choices while a load is on its way."""

    runner: RequestRunner
    held: list[HeldCall] = field(default_factory=list)
    sent: list[Request] = field(default_factory=list)

    def submit(
        self,
        request: Request,
        on_done: Callable[[Response], None],
        _on_progress: object = None,
        _on_started: object = None,
    ) -> HeldCall:
        """Hold the call."""
        self.sent.append(request)
        call = HeldCall(request, on_done)
        self.held.append(call)
        return call

    def release(self) -> None:
        """Answer every held call in order (a cancelled one with `cancelled`, as a pool would)."""
        calls = self.held
        self.held = []
        for call in calls:
            cancelled = call.cancelled
            response = cancelled_response(call.request) if cancelled else self.runner.execute(call.request, None, None)
            call.answered = True
            call.on_done(response)


def held_model(leg: Leg) -> tuple[DownloadModel, HeldExecutor]:
    """Return the first batch's download view-model over an executor that holds its calls."""
    executor = HeldExecutor(leg.backend)
    model = DownloadModel(executor, Activity(executor), DialogOutlet(), FIRST_BATCH_ID, ManualDeferral())
    return model, executor
