"""The scan of the chosen folder: progress, Cancel, and the tree it gives (architecture §3.2).

Qt-free. `scan_tree` runs on the GUI's own scan pool on a `latest` channel, so a new folder
cancels the scan in flight and a scan never waits behind uploads or downloads. The page shows
the progress events while it runs; Cancel stops it between folders ("stopped", never shown as
a failure). The answer is indexed once (`ScanIndex`) for the tree.
"""

from collections.abc import Callable
from functools import partial

from transcribe_ui.api.classes import ErrorResponse, ProgressEvent, Response
from transcribe_ui.api.constants import ERROR_CANCELLED
from transcribe_ui.discovery.classes import ScanSpec, ScanTreeRequest, ScanTreeResponse
from transcribe_ui.gui.executor import CallHandle, Executor, LatestChannel
from transcribe_ui.gui.feedback import ErrorView, error_view
from transcribe_ui.gui.selection.constants import SCAN_KEY, ScanStatus
from transcribe_ui.gui.selection.tree import ScanIndex, Scanned

# Told about a scan that answered.
type OnScanned = Callable[[Scanned], None]


def scan_request(spec: ScanSpec) -> ScanTreeRequest:
    """Return the `scan_tree` request of a scan."""
    return ScanTreeRequest(action="scan_tree", **spec.model_dump())


class ScanModel:
    """The folder being scanned or shown, the scan's state and progress, and the scanned tree."""

    def __init__(self, executor: Executor, notify: Callable[[], None], on_scanned: OnScanned) -> None:
        """Scan through `executor`; `notify` tells the page about a change, `on_scanned` about a new tree."""
        self._channel = LatestChannel(executor)
        self._notify = notify
        self._on_scanned = on_scanned
        self.spec: ScanSpec | None = None
        self.status: ScanStatus = "idle"
        # Media files found so far by the running scan.
        self.found = 0
        self.scanned: Scanned | None = None
        # The error of a failed scan, or None.
        self.error: ErrorView | None = None
        # Counts the trees shown, so the tree model resets only for a new one.
        self.version = 0
        self._call: CallHandle | None = None

    def start(self, spec: ScanSpec) -> None:
        """Scan a folder (a scan in flight is cancelled); the tree on screen goes until it answers."""
        self.spec = spec
        self.status = "scanning"
        self.found = 0
        self.error = None
        self.scanned = None
        self.version += 1
        self._notify()
        progress = partial(self._progress, spec)
        done = partial(self._done, spec)
        self._call = self._channel.send(SCAN_KEY, scan_request(spec), done, progress)

    def cancel(self) -> None:
        """Stop the running scan between two folders."""
        if self._call is not None:
            self._call.cancel()

    def _progress(self, spec: ScanSpec, event: ProgressEvent) -> None:
        if spec == self.spec:
            self.found = event.completed
            self._notify()

    def _done(self, spec: ScanSpec, response: Response) -> None:
        if spec != self.spec:
            return
        self._call = None
        if isinstance(response, ScanTreeResponse):
            self.status = "ready"
            self.scanned = Scanned(spec, ScanIndex.of(response))
            self.version += 1
            self._on_scanned(self.scanned)
        elif isinstance(response, ErrorResponse):
            cancelled = response.error.code == ERROR_CANCELLED
            self.status = "stopped" if cancelled else "failed"
            self.error = None if cancelled else error_view(response)
        self._notify()
