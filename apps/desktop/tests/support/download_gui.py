"""The download dialog's view-model over the seeded download batch, with recording runners (plan m6 M6.9)."""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from support.batches import FIRST_BATCH_ID
from support.jobs import RecordingOutlet
from support.parity import Leg
from transcribe_ui.api.classes import ProgressEvent, Request, Response
from transcribe_ui.api.ports import CancelFlag, CancelToken, ProgressSink
from transcribe_ui.gui.activity import Activity
from transcribe_ui.gui.download.state import DialogData, dialog_data
from transcribe_ui.gui.download.viewmodel import DownloadModel
from transcribe_ui.gui.executor import RequestRunner, SyncExecutor


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
class DownloadArea:
    """The download dialog's view-model of the first batch on `SyncExecutor`, its activity and its outlet."""

    leg: Leg
    runner: Recording
    outlet: RecordingOutlet
    activity: Activity
    model: DownloadModel

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
    outlet = RecordingOutlet()
    model = DownloadModel(executor, activity, outlet, FIRST_BATCH_ID)
    return DownloadArea(leg, runner, outlet, activity, model)
