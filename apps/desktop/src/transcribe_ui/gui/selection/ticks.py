"""The selection of the New job page: the ordered operations and what the backend makes of them (§3.2).

Qt-free. Each tick or untick appends one `include` or `exclude` operation (with the filters
active then, for a folder) and sends `preview_selection` with every operation on the scan on
screen, on a `latest` channel: under fast clicking only the last preview's answer counts. The
tick boxes, the folders' partial state and the totals come from that answer alone. Until it
arrives, a row the user just clicked shows what the click asked for (ticked for `include`,
unticked for `exclude`), so a second click on it undoes the first instead of repeating it.
"""

from collections.abc import Callable, Mapping
from functools import partial
from types import MappingProxyType

from transcribe_ui.api.classes import ErrorResponse, Response
from transcribe_ui.api.constants import ERROR_CANCELLED
from transcribe_ui.discovery.classes import (
    PreviewSelectionResponse,
    ScanWarning,
    SelectionCounts,
    SelectionOperation,
)
from transcribe_ui.gui.constants import CALL_DONE
from transcribe_ui.gui.executor import CallHandle, Executor, LatestChannel
from transcribe_ui.gui.feedback import ErrorView, error_view
from transcribe_ui.gui.selection.constants import CHECKED, PREVIEW_KEY, UNCHECKED, CheckMark
from transcribe_ui.gui.selection.marks import Marks
from transcribe_ui.gui.selection.tree import Scanned


class TickModel:
    """The selection operations in order, the latest preview's tick boxes, totals and warnings."""

    def __init__(self, executor: Executor, notify: Callable[[], None]) -> None:
        """Send the previews through `executor`; `notify` tells the page about a change."""
        self._channel = LatestChannel(executor)
        self._notify = notify
        self.operations: tuple[SelectionOperation, ...] = ()
        self.marks = Marks()
        # Totals of the latest preview (None before the first answer on this scan).
        self.counts: SelectionCounts | None = None
        # The latest preview's warnings: the scan's own, then operations whose path holds no media file.
        self.warnings: tuple[ScanWarning, ...] = ()
        # The error of the latest preview, or None.
        self.error: ErrorView | None = None
        # Counts the changes of the tick boxes, so the tree repaints only then.
        self.version = 0
        # What each row clicked since the latest answer asked for.
        self._pending: dict[str, CheckMark] = {}
        self._scanned: Scanned | None = None
        # The preview in flight (None: none), and the number of the latest send; older answers do not count.
        self._call: CallHandle | None = None
        self._generation = 0

    @property
    def busy(self) -> bool:
        """Return whether a preview is in flight (the tick boxes and totals may still change)."""
        return self._call is not None

    @property
    def pending(self) -> Mapping[str, CheckMark]:
        """Return what each row clicked since the latest answer asked for (a copy)."""
        return MappingProxyType(dict(self._pending))

    def start(self, scanned: Scanned | None, operations: tuple[SelectionOperation, ...]) -> None:
        """Use these operations on a scan (a new scan, or a reopened job) and preview them.

        Without a scan (one is on its way) the preview in flight for the previous scan is dropped.
        """
        self._scanned = scanned
        self.operations = operations
        self.marks = Marks()
        self.counts = None
        self.warnings = ()
        self._pending = {}
        self.preview()

    def mark(self, path: str, *, folder: bool) -> CheckMark:
        """Return a row's tick box: what a click asked for until the preview answers, else the preview's."""
        pending = self._pending.get(path)
        return self.marks.mark(path, folder=folder) if pending is None else pending

    def add(self, operation: SelectionOperation) -> None:
        """Append an operation and preview the selection."""
        self.operations = (*self.operations, operation)
        self._pending[operation.path] = CHECKED if operation.operation == "include" else UNCHECKED
        self.preview()

    def clear(self) -> None:
        """Drop every operation and preview the empty selection."""
        self.operations = ()
        self._pending = {}
        self.preview()

    def preview(self) -> None:
        """Send the preview of the operations on the scan (the previous one in flight is cancelled)."""
        self.error = None
        self.version += 1
        self._generation += 1
        if self._call is not None:
            self._call.cancel()
            self._call = None
        if self._scanned is not None:
            request = self._scanned.preview_request(self.operations)
            answered = partial(self._previewed, self._generation)
            call = self._channel.send(PREVIEW_KEY, request, answered)
            # `SyncExecutor` has answered already.
            self._call = None if call.state == CALL_DONE else call
        self._notify()

    def _previewed(self, generation: int, response: Response) -> None:
        if generation != self._generation:
            return
        self._call = None
        if isinstance(response, PreviewSelectionResponse):
            self.marks = Marks.of(response)
            self.counts = response.counts
            self.warnings = response.warnings
        elif isinstance(response, ErrorResponse):
            if response.error.code == ERROR_CANCELLED:
                # Only the window's closing cancels the latest preview.
                return
            self.error = error_view(response)
        self._pending = {}
        self.version += 1
        self._notify()
