"""The download dialog's view-model (architecture §3.1 "GUI" "Download").

Qt-free. One per batch, kept while the window lives, so the dialog reopens with the user's
choices. The first opening loads the settings (`get_settings`) and the batch (`get_batch`) side
by side and, once both answered, pre-fills the choices from the settings' placement defaults
(`mirror` for a job uploaded from another computer: the summary's `adopted`); only then does it plan,
so no plan of choices about to change is sent. Every later opening loads the batch (for the
step header) and the plan.
Each change of a choice asks for the plan again (`plan_download`, or for a custom plan the
`check_only` import of its file), latest wins, so the table always shows what the download would
do with the current choices; the folder count and level ticks wait `PLAN_DELAY_MS` for the next
step or tick first (`Deferral`), and a plan of choices changed since is cancelled and ignored. Download runs in the activity panel (`running.py`); the custom
plan is exported to a file the user picks (`PlanExport`).
"""

from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Unpack

from transcribe_ui.api.classes import GetSettingsRequest, Response
from transcribe_ui.batches.classes import GetBatchRequest
from transcribe_ui.gui.activity import Activity
from transcribe_ui.gui.constants import BATCHES_CHANGED_SIGNAL
from transcribe_ui.gui.download import constants
from transcribe_ui.gui.download.export import PlanExport
from transcribe_ui.gui.download.form import (
    DownloadForm,
    FormChanges,
    download_request,
    first_choices,
    form_hint,
    plan_request,
)
from transcribe_ui.gui.download.loading import Deferral, Loaded, after_load, failed_some
from transcribe_ui.gui.download.running import DownloadOutlet, DownloadRun
from transcribe_ui.gui.executor import CallHandle, Executor, LatestChannel
from transcribe_ui.gui.viewmodel import ViewModel


class DownloadModel(ViewModel):
    """One batch's download dialog: the choices, the plan they make, the custom plan, and the download."""

    def __init__(
        self,
        executor: Executor,
        activity: Activity,
        outlet: DownloadOutlet,
        batch_id: str,
        deferral: Deferral,
    ) -> None:
        """Load through `executor`; download in `activity`; tell the window through `outlet`; delay through `deferral`."""
        super().__init__(executor)
        self.batch_id = batch_id
        self._outlet = outlet
        self._deferral = deferral
        self._loads = LatestChannel(executor)
        self.form = DownloadForm()
        # The imported custom plan file, or None.
        self.plan_path: str | None = None
        self.loaded = Loaded()
        self.export = PlanExport(executor, batch_id, self.changed)
        self.run = DownloadRun(activity, outlet, batch_id, self.changed, self._downloaded)
        self._state = _ModelState()

    def on_open(self) -> None:
        """Pre-fill the choices (the first time only: from the settings and the batch), then load the batch and the plan."""
        state = self._state
        if state.settings_asked:
            if state.filled:
                # Else the pending pre-fill loads the plan.
                self.reload()
            return
        state.settings_asked = True
        settings = GetSettingsRequest(action="get_settings")
        self._loads.send(constants.SETTINGS_KEY, settings, self._first_answer)
        batch = GetBatchRequest(action="get_batch", batch_id=self.batch_id)
        self._loads.send(constants.FIRST_BATCH_KEY, batch, self._first_answer)

    def reload(self, *, batch: bool = True, soon: bool = False) -> None:
        """Load the batch (unless `batch` is false) and the plan of the current choices (`soon`: after the delay).

        The plan of earlier choices still on its way is cancelled and its answer ignored.
        """
        state = self._state
        self._deferral.cancel()
        state.generation += 1
        if state.plan_call is not None:
            state.plan_call.cancel()
            state.plan_call = None
        if batch:
            loaded = GetBatchRequest(action="get_batch", batch_id=self.batch_id)
            self._loads.send(constants.BATCH_KEY, loaded, partial(self._loaded, None))
        request = plan_request(self.batch_id, self.form, self.plan_path)
        self.loaded = replace(self.loaded, loading=request is not None)
        self.changed()
        if request is None:
            return
        if soon:
            self._deferral.call_later(partial(self.reload, batch=False))
            return
        answered = partial(self._loaded, state.generation)
        state.plan_call = self._loads.send(constants.PLAN_KEY, request, answered)

    def edit(self, *, soon: bool = False, **changes: Unpack[FormChanges]) -> None:
        """Take changed choices and ask for their plan (`soon`: after the delay, for steps and ticks in a row)."""
        form = replace(self.form, **changes)
        if form == self.form:
            return
        if form.strategy != self.form.strategy:
            self.export.clear()
        self.form = form
        self._state.edited = True
        self.reload(batch=False, soon=soon)

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
        state = self._state
        if not isinstance(batch_ids, tuple) or state.emitting:
            return
        if batch_ids and self.batch_id not in batch_ids:
            return
        if state.filled:
            self.reload()
        elif state.settings_asked:
            # The pre-fill plans once its answers are in; it then loads the batch again too.
            state.changed_meanwhile = True

    def _first_answer(self, response: Response) -> None:
        """Take an answer the first choices wait for; once both are in, pre-fill the choices and plan.

        Without settings (unreadable file) the dialog starts from the request's defaults; late
        answers never replace choices the user has made meanwhile.
        """
        state = self._state
        if response.action == "get_settings":
            state.settings = response
        else:
            state.first_batch = response
            # Shows the step header (or the load error, with Retry).
            self._loaded(None, response)
        if state.settings is None or state.first_batch is None or state.filled:
            return
        state.filled = True
        if not state.edited:
            self.form = first_choices(state.settings, state.first_batch)
        self.reload(batch=state.changed_meanwhile)

    def _loaded(self, generation: int | None, response: Response) -> None:
        # A plan (`generation` set) of choices changed since is ignored; batch loads always count.
        if generation is None or generation == self._state.generation:
            self.loaded = after_load(self.loaded, response)
            self.changed()

    def _downloaded(self, response: Response) -> None:
        # Whatever the answer (done, stopped, failed), outcomes may have been recorded: every page
        # showing the batch reloads, and so does the dialog (the plan then shows what is placed).
        # (Signals reach their slots before `emit` returns; an exception in a slot stays in Qt.)
        self._state.emitting = True
        self._outlet.emit(BATCHES_CHANGED_SIGNAL, (self.batch_id,))
        self._state.emitting = False
        self.reload()
        if failed_some(response):
            # Failures would hide behind the activity panel's "Finished": show them.
            self._outlet.show_dialog()


@dataclass
class _ModelState:
    """The view-model's bookkeeping, apart from what the dialog shows."""

    # `get_settings` was sent (the pre-fill happens once).
    settings_asked: bool = False
    # The answers the first choices wait for (None until each arrived), and whether they were made.
    settings: Response | None = None
    first_batch: Response | None = None
    filled: bool = False
    # The batch changed elsewhere before the pre-fill: it loads the batch again then.
    changed_meanwhile: bool = False
    # The user changed a choice (a late settings answer then pre-fills nothing).
    edited: bool = False
    # Number of the latest plan load; an answer of an older one is ignored.
    generation: int = 0
    # The plan load in flight, cancelled when the choices change.
    plan_call: CallHandle | None = None
    # Set while this dialog tells the window about its own change, which it has reloaded for already.
    emitting: bool = False


def can_download(model: DownloadModel) -> bool:
    """Return whether Download can start: nothing missing, the plan of the current choices shown without errors."""
    plan = model.loaded.plan
    if form_hint(model.form, model.plan_path) is not None:
        return False
    if plan is None or model.loaded.loading or plan.errors:
        return False
    return not model.run.busy()
