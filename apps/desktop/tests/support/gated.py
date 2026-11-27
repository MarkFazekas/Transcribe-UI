"""A fake backend for the GUI bridge tests: records every call, holds gated actions until released."""

import threading
from collections.abc import Callable, Iterable

from pytestqt.qtbot import QtBot

from transcribe_ui.api.classes import GetAppInfoResponse, Request, Response
from transcribe_ui.api.ports import CancelToken, ProgressSink
from transcribe_ui.batches.classes import ListBatchesRequest
from transcribe_ui.gui.executor import cancelled_response
from transcribe_ui.runs.classes import CancelWorkerRequest, RefreshStatusRequest
from transcribe_ui.upload.classes import RunUploadRequest

# How often a held call looks at its gate and its cancel flag, in seconds.
POLL_S = 0.005
# How long a test waits for the event loop, in milliseconds.
WAIT_MS = 10_000
# How long a test waits for the pool threads to end, in milliseconds.
DRAIN_MS = 5000
# Action of the long upload request.
UPLOAD = "run_upload"
# Action of the poll request.
REFRESH = "refresh_status"
# A batch id the fake requests name.
BATCH_ID = "batch_20261127_120000"
# A run id the fake requests name.
RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def upload(batch_id: str = BATCH_ID) -> RunUploadRequest:
    """Return a `run_upload` request (long pool)."""
    return RunUploadRequest(action=UPLOAD, batch_id=batch_id)


def uploads(count: int) -> list[RunUploadRequest]:
    """Return `count` uploads of distinct batches."""
    return [upload(f"batch_20261127_1200{index:02d}") for index in range(count)]


def stop() -> CancelWorkerRequest:
    """Return a `cancel_worker` request (short pool: Stop)."""
    return CancelWorkerRequest(action="cancel_worker", run_id=RUN_ID)


def refresh() -> RefreshStatusRequest:
    """Return a `refresh_status` request (poll pool)."""
    return RefreshStatusRequest(action=REFRESH)


def listing() -> ListBatchesRequest:
    """Return a `list_batches` request (short pool)."""
    return ListBatchesRequest(action="list_batches")


class GatedRunner:
    """`RequestRunner` whose gated actions run until released (or cancelled); thread-safe."""

    def __init__(self, gated: Iterable[str] = (), *, honour_cancel: bool = True) -> None:
        """Hold the calls of these actions (until released only, if `honour_cancel` is False)."""
        self._gated = frozenset(gated)
        self._honour_cancel = honour_cancel
        self._lock = threading.Lock()
        self._release = threading.Event()
        # Actions in the order their calls reached `execute`.
        self.calls: list[str] = []
        # Requests in the order their calls reached `execute`.
        self.requests: list[Request] = []
        # Actions whose call saw its cancel flag set while held.
        self.cancelled: list[str] = []

    def execute(self, request: Request, progress: ProgressSink | None, cancel: CancelToken | None) -> Response:
        """Record the call; hold a gated one until `release` or its cancel flag."""
        assert progress is not None
        assert cancel is not None
        with self._lock:
            self.calls.append(request.action)
            self.requests.append(request)
        if request.action in self._gated:
            return self._hold(request, cancel)
        return _answer()

    def release(self) -> None:
        """Let every held call (and later ones) finish."""
        self._release.set()

    def started(self, action: str) -> int:
        """Return how many calls of `action` reached `execute`."""
        with self._lock:
            return self.calls.count(action)

    def _hold(self, request: Request, cancel: CancelToken) -> Response:
        while not self._release.wait(POLL_S):
            if self._honour_cancel and cancel.is_cancelled():
                with self._lock:
                    self.cancelled.append(request.action)
                return cancelled_response(request)
        return _answer()


def _answer() -> Response:
    return GetAppInfoResponse(app_version="test", contracts_schema_version="test")


def wait_for(qtbot: QtBot, condition: Callable[[], bool]) -> None:
    """Process events until `condition` holds (fails after `WAIT_MS`)."""
    qtbot.waitUntil(condition, timeout=WAIT_MS)
