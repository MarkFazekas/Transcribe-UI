"""The export of a batch's custom plan to a file (architecture §3.1 "GUI" "Download").

Qt-free. Its error shows in the custom plan's panel with a Retry that exports again to the same
file; it is forgotten when a new export starts or the strategy changes.
"""

from collections.abc import Callable
from pathlib import Path

from transcribe_ui.api.classes import ErrorResponse, Response
from transcribe_ui.gui.download import constants
from transcribe_ui.gui.executor import ExclusiveChannel, Executor
from transcribe_ui.gui.feedback import ErrorView, error_view
from transcribe_ui.placement.classes import ExportCustomPlanRequest, ExportCustomPlanResponse


class PlanExport:
    """The export of the batch's custom plan to a file the user picked."""

    def __init__(self, executor: Executor, batch_id: str, on_change: Callable[[], None]) -> None:
        """Send through `executor`, one export at a time; `on_change` is told about each answer."""
        self._channel = ExclusiveChannel(executor)
        self._batch_id = batch_id
        self._on_change = on_change
        # The file the plan was written to, or None.
        self.path: str | None = None
        # The error of the last export (shown with a Retry that exports again), or None.
        self.error: ErrorView | None = None
        # The file the last export was sent for (Retry exports there again).
        self._target: str | None = None

    def build_request(self, path: str) -> ExportCustomPlanRequest:
        """Return the export of the plan to `path` (made absolute: a file dialog's answer)."""
        target = str(Path(path).absolute())
        return ExportCustomPlanRequest(action="export_custom_plan", batch_id=self._batch_id, path=target)

    def export(self, path: str) -> None:
        """Write the custom plan to `path`, pre-filled with the mirror folders and the default names."""
        self._target = path
        self.clear()
        self._channel.send(constants.EXPORT_KEY, self.build_request(path), self._exported)

    def retry(self) -> None:
        """Export again to the file of the last export."""
        if self._target is not None:
            self.export(self._target)

    def clear(self) -> None:
        """Forget the error of the last export (a new export, or another strategy)."""
        if self.error is not None:
            self.error = None
            self._on_change()

    def _exported(self, response: Response) -> None:
        if isinstance(response, ExportCustomPlanResponse):
            self.path = response.path
            self.error = None
        elif isinstance(response, ErrorResponse):
            self.error = error_view(response)
        self._on_change()
