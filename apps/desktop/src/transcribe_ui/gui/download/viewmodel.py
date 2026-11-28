"""The download dialog's view-model (architecture §3.1 "GUI" "Download").

Qt-free. One per batch, kept while the window lives, so the dialog reopens with the user's
choices. The first opening pre-fills the choices from the settings' placement defaults
(`get_settings`); every opening loads the batch (`get_batch`, for the step header) and the plan.
Each change of a choice asks for the plan again (`plan_download`, or for a custom plan the
`check_only` import of its file), latest wins, so the table always shows what the download would
do with the current choices. Download runs in the activity panel (`running.py`); the custom
plan is exported to a file the user picks (`PlanExport`).
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Unpack

from transcribe_ui.api.classes import ErrorResponse, GetSettingsRequest, GetSettingsResponse, Response
from transcribe_ui.batches.classes import GetBatchRequest
from transcribe_ui.batches.responses import BatchSummary, GetBatchResponse
from transcribe_ui.gui.activity import Activity
from transcribe_ui.gui.download import constants
from transcribe_ui.gui.download.form import (
    DownloadForm,
    FormChanges,
    download_request,
    form_from_settings,
    form_hint,
    plan_request,
)
from transcribe_ui.gui.download.running import DownloadOutlet, DownloadRun
from transcribe_ui.gui.executor import ExclusiveChannel, Executor, LatestChannel
from transcribe_ui.gui.feedback import ErrorView, error_view
from transcribe_ui.gui.viewmodel import ViewModel
from transcribe_ui.placement.classes import (
    ExportCustomPlanRequest,
    ExportCustomPlanResponse,
    FolderLevel,
    ImportCustomPlanResponse,
    PlacementPlan,
    PlanDownloadResponse,
)


@dataclass(frozen=True)
class Loaded:
    """What the dialog loaded: the batch, its folder levels and the plan of the current choices."""

    summary: BatchSummary | None = None
    levels: tuple[FolderLevel, ...] = ()
    # The plan of the current choices (or of the imported custom plan); None before one arrived.
    plan: PlacementPlan | None = None
    # Whether a plan for the current choices is on its way (the plan shown may be the previous one).
    loading: bool = False
    # The error of the last load, shown in the dialog with Retry; None after a load that succeeded.
    error: ErrorView | None = None


class PlanExport:
    """The export of the batch's custom plan to a file the user picked."""

    def __init__(self, executor: Executor, batch_id: str, on_change: Callable[[], None]) -> None:
        """Send through `executor`, one export at a time; `on_change` is told about each answer."""
        self._channel = ExclusiveChannel(executor)
        self._batch_id = batch_id
        self._on_change = on_change
        # The file the plan was written to, or None.
        self.path: str | None = None
        # The error of the last export, or None.
        self.error: ErrorView | None = None

    def build_request(self, path: str) -> ExportCustomPlanRequest:
        """Return the export of the plan to `path` (made absolute: a file dialog's answer)."""
        target = str(Path(path).absolute())
        return ExportCustomPlanRequest(action="export_custom_plan", batch_id=self._batch_id, path=target)

    def export(self, path: str) -> None:
        """Write the custom plan to `path`, pre-filled with the mirror folders and the default names."""
        self._channel.send(constants.EXPORT_KEY, self.build_request(path), self._exported)

    def _exported(self, response: Response) -> None:
        if isinstance(response, ExportCustomPlanResponse):
            self.path = response.path
            self.error = None
        elif isinstance(response, ErrorResponse):
            self.error = error_view(response)
        self._on_change()


class DownloadModel(ViewModel):
    """One batch's download dialog: the choices, the plan they make, the custom plan, and the download."""

    def __init__(self, executor: Executor, activity: Activity, outlet: DownloadOutlet, batch_id: str) -> None:
        """Load through `executor`; download in `activity`; ask and tell the window through `outlet`."""
        super().__init__(executor)
        self.batch_id = batch_id
        self._outlet = outlet
        self._loads = LatestChannel(executor)
        self.form = DownloadForm()
        # The imported custom plan file, or None.
        self.plan_path: str | None = None
        self.loaded = Loaded()
        self.export = PlanExport(executor, batch_id, self.changed)
        self.run = DownloadRun(activity, outlet, batch_id, self.changed, self._downloaded)
        self._settings_read = False
        # Set while this dialog tells the window about its own change, which it has reloaded for already.
        self._emitting = False

    def on_open(self) -> None:
        """Pre-fill the choices from the settings (the first time only), then load the batch and the plan."""
        if self._settings_read:
            self.reload()
            return
        settings = GetSettingsRequest(action="get_settings")
        self._loads.send(constants.SETTINGS_KEY, settings, self._settings_loaded)

    def reload(self, *, batch: bool = True) -> None:
        """Load the batch (unless `batch` is false) and the plan of the current choices (the last load in flight is replaced)."""
        if batch:
            loaded = GetBatchRequest(action="get_batch", batch_id=self.batch_id)
            self._loads.send(constants.BATCH_KEY, loaded, self._loaded)
        request = plan_request(self.batch_id, self.form, self.plan_path)
        if request is None:
            self.loaded = replace(self.loaded, plan=None, loading=False)
        else:
            self.loaded = replace(self.loaded, loading=True)
        self.changed()
        if request is not None:
            self._loads.send(constants.PLAN_KEY, request, self._loaded)

    def edit(self, **changes: Unpack[FormChanges]) -> None:
        """Take changed choices and ask for their plan."""
        form = replace(self.form, **changes)
        if form != self.form:
            self.form = form
            self.reload(batch=False)

    def import_plan(self, path: str) -> None:
        """Check the edited custom plan file at `path` (made absolute: a file dialog's answer) and show its plan."""
        self.plan_path = str(Path(path).absolute())
        self.reload(batch=False)

    def download(self) -> bool:
        """Start the download the choices make, while the plan allows it (`can_download`); return whether it started."""
        request = download_request(self.batch_id, self.form, self.plan_path)
        if request is None or not can_download(self):
            return False
        return self.run.start(request)

    def on_batches_changed(self, batch_ids: object) -> None:
        """Load again when the batch changed elsewhere (an empty tuple: any batch)."""
        if not isinstance(batch_ids, tuple) or self._emitting:
            return
        if not batch_ids or self.batch_id in batch_ids:
            self.reload()

    def _settings_loaded(self, response: Response) -> None:
        # Without settings (unreadable file) the dialog starts from the request's defaults.
        if isinstance(response, GetSettingsResponse):
            self.form = form_from_settings(response.settings.placement)
        self._settings_read = True
        self.reload()

    def _loaded(self, response: Response) -> None:
        self.loaded = _after_load(self.loaded, response)
        self.changed()

    def _downloaded(self, _response: Response) -> None:
        # Whatever the answer (done, stopped, failed), outcomes may have been recorded: every page
        # showing the batch reloads, and so does the dialog (the plan then shows what is placed).
        # (Signals reach their slots before `emit` returns; an exception in a slot stays in Qt.)
        self._emitting = True
        self._outlet.emit(constants.BATCHES_CHANGED_SIGNAL, (self.batch_id,))
        self._emitting = False
        self.reload()


def _after_error(loaded: Loaded, response: ErrorResponse) -> Loaded:
    error = error_view(response)
    if response.action == "get_batch":
        return replace(loaded, error=error)
    # The plan of the current choices could not be made: none is shown.
    return replace(loaded, plan=None, loading=False, error=error)


def _after_load(loaded: Loaded, response: Response) -> Loaded:
    """Return what the dialog has loaded once a load answered."""
    if isinstance(response, GetBatchResponse):
        return replace(loaded, summary=response.batch.summary)
    if isinstance(response, PlanDownloadResponse):
        return Loaded(summary=response.batch, levels=response.folder_levels, plan=response.plan)
    if isinstance(response, ImportCustomPlanResponse):
        return Loaded(summary=response.batch, levels=loaded.levels, plan=response.plan)
    if isinstance(response, ErrorResponse):
        return _after_error(loaded, response)
    return loaded


def can_download(model: DownloadModel) -> bool:
    """Return whether Download can start: nothing missing, the plan of the current choices shown without errors."""
    plan = model.loaded.plan
    if form_hint(model.batch_id, model.form, model.plan_path) is not None:
        return False
    if plan is None or model.loaded.loading or plan.errors:
        return False
    return not model.run.busy()
