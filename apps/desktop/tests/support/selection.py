"""The New job page's view-model over a seeded backend with the media tree, and executors to script it (M6.4)."""

import json
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from support.discovery import build_media_tree, media_dir
from support.fakes import seeded_ports
from support.jobs import RecordingOutlet, RecordingRunner
from support.normalise import PathNormaliser
from transcribe_ui.api.backend import Backend
from transcribe_ui.api.classes import Request, Response
from transcribe_ui.api.ports import CancelToken, Ports, ProgressSink
from transcribe_ui.gui.constants import CALL_DONE, CALL_RUNNING, CallState
from transcribe_ui.gui.executor import (
    CallHandle,
    DoneCallback,
    ProgressCallback,
    StartedCallback,
    SyncExecutor,
    cancelled_response,
)
from transcribe_ui.gui.selection.constants import CheckMark
from transcribe_ui.gui.selection.rows import cell_text, row_mark
from transcribe_ui.gui.selection.tree import ScanIndex
from transcribe_ui.gui.selection.viewmodel import NewJobModel

# How often a held upload looks at its gate and its cancel flag, in seconds.
HOLD_POLL_S = 0.005
# The long request the held runner keeps running.
HELD_ACTION = "run_upload"


def media_ports(root: Path) -> Ports:
    """Return seeded ports with the media tree under `root/media` (close `state` when done)."""
    build_media_tree(root)
    return seeded_ports(root)


def media_path(root: Path, *parts: str) -> str:
    """Return the path of an entry of the media tree, as the scan reports it."""
    return str(media_dir(root).joinpath(*parts))


@dataclass
class SelectionArea:
    """The New job page's view-model with its recording runner and outlet."""

    runner: RecordingRunner
    outlet: RecordingOutlet
    model: NewJobModel

    def sent(self, action: str) -> list[Request]:
        """Return the requests of `action` sent so far."""
        return [request for request in self.runner.requests if request.action == action]


@dataclass(frozen=True)
class ScannedPage:
    """The view-model with the media tree scanned, addressed by the parts of a path below `root/media`."""

    area: SelectionArea
    root: Path

    @property
    def model(self) -> NewJobModel:
        """Return the view-model."""
        return self.area.model

    @property
    def index(self) -> ScanIndex:
        """Return the index of the scan on screen."""
        scanned = self.model.scan.scanned
        assert scanned is not None
        return scanned.index

    def path(self, *parts: str) -> str:
        """Return the path of an entry of the media tree."""
        return media_path(self.root, *parts)

    def node(self, *parts: str) -> int:
        """Return the tree node of an entry."""
        index = self.index
        path = self.path(*parts)
        nodes = range(index.folder_count + len(index.tree.files))
        return next(node for node in nodes if index.path(node) == path)

    def mark(self, *parts: str) -> CheckMark:
        """Return the tick box of an entry's row."""
        return row_mark(self.model, self.index, self.node(*parts))

    def cell(self, column: int, *parts: str) -> str:
        """Return the text of a cell of an entry's row."""
        return cell_text(self.model, self.index, self.node(*parts), column)

    def tick(self, *parts: str, folder: bool = True) -> None:
        """Click the tick box of a folder (or, with `folder` False, a file)."""
        self.model.tick(self.path(*parts), folder=folder)


def scanned_page(area: SelectionArea, root: Path) -> ScannedPage:
    """Choose the media tree's folder and return the page over it."""
    area.model.choose_folder(media_path(root))
    return ScannedPage(area, root)


def selection_area(backend: Backend) -> SelectionArea:
    """Return the New job view-model on `SyncExecutor` over `backend`, its settings loaded."""
    runner = RecordingRunner(backend)
    outlet = RecordingOutlet()
    model = NewJobModel(SyncExecutor(runner), outlet)
    model.settings.load()
    return SelectionArea(runner, outlet, model)


def documents(requests: Sequence[Request], root: Path) -> str:
    """Return requests as the JSON of a golden file, paths below `root` as `<root>/...`."""
    context = PathNormaliser([root]).context()
    dumped = [request.model_dump(mode="json", context=context) for request in requests]
    return f"{json.dumps(dumped, indent=2, ensure_ascii=False)}\n"


@dataclass
class DeferredCall:
    """A call that runs only when the test answers it; a cancel before that only sets its flag."""

    request: Request
    on_done: DoneCallback
    cancelled: bool = False
    answered: bool = False

    @property
    def state(self) -> CallState:
        """Return "running" until answered (a running call ignores its flag until it answers)."""
        return CALL_DONE if self.answered else CALL_RUNNING

    def cancel(self) -> None:
        """Set the cancel flag."""
        self.cancelled = True


@dataclass
class DeferredExecutor:
    """`Executor` whose calls wait until the test answers them, in any order."""

    backend: Backend
    calls: list[DeferredCall] = field(default_factory=list)

    def submit(
        self,
        request: Request,
        on_done: DoneCallback,
        _on_progress: ProgressCallback | None = None,
        on_started: StartedCallback | None = None,
    ) -> CallHandle:
        """Hold the call until `answer`."""
        if on_started is not None:
            on_started()
        call = DeferredCall(request, on_done)
        self.calls.append(call)
        return call

    def pending(self, action: str) -> list[DeferredCall]:
        """Return the calls of `action` not answered yet, in the order sent."""
        return [call for call in self.calls if call.request.action == action and not call.answered]

    def answer(self, call: DeferredCall) -> None:
        """Run the call on the backend (a cancelled one answers `cancelled`) and deliver its answer."""
        call.answered = True
        response = cancelled_response(call.request) if call.cancelled else self.backend.execute(call.request)
        call.on_done(response)

    def answer_all(self) -> None:
        """Answer every call not answered yet, oldest first, until none is left."""
        waiting = self._waiting()
        while waiting:
            self.answer(waiting[0])
            waiting = self._waiting()

    def _waiting(self) -> list[DeferredCall]:
        return [call for call in self.calls if not call.answered]


class HeldUploads:
    """`RequestRunner` that holds every upload until released (or cancelled) and runs the rest on a backend."""

    def __init__(self, backend: Backend) -> None:
        """Run the other requests on `backend`."""
        self._backend = backend
        self._release = threading.Event()
        self._lock = threading.Lock()
        self.held = 0

    def execute(self, request: Request, progress: ProgressSink | None, cancel: CancelToken | None) -> Response:
        """Hold an upload; run anything else at once."""
        if request.action != HELD_ACTION:
            return self._backend.execute(request, progress, cancel)
        with self._lock:
            self.held += 1
        while not self._release.wait(HOLD_POLL_S):
            if cancel is not None and cancel.is_cancelled():
                break
        return cancelled_response(request)

    def release(self) -> None:
        """Let every held upload end."""
        self._release.set()
