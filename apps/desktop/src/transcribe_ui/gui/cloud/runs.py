"""View-model of the runs the Cloud page shows: the current run and the history (§3.1 "GUI").

Qt-free. The run list (`list_runs`, database only) is what the page shows: it loads at start-up,
after every check of the cloud and after every action that changes a run, on a latest-wins
channel, so the answer shown is always that of the last load sent (an older answer arriving
late is dropped). The current run is the one that is not final, else the last one with its
outcome. When a listed run turns final, the window flashes in the task bar and the jobs the run
covered reload.
"""

from collections.abc import Callable
from contextlib import ExitStack

from transcribe_ui.api.classes import ErrorResponse, Response
from transcribe_ui.api.constants import ERROR_CANCELLED
from transcribe_ui.gui.cloud.constants import LIST_KEY, RUN_HISTORY_LIMIT, RUNS_CHANGED_SIGNAL
from transcribe_ui.gui.cloud.display import is_final
from transcribe_ui.gui.cloud.ports import CloudOutlet
from transcribe_ui.gui.constants import BATCHES_CHANGED_SIGNAL
from transcribe_ui.gui.executor import Executor, LatestChannel
from transcribe_ui.gui.feedback import ErrorView, error_view
from transcribe_ui.gui.viewmodel import ViewModel
from transcribe_ui.runs.classes import ListRunsRequest
from transcribe_ui.runs.responses import ListRunsResponse, RunProgress

# Called once a start-up step's request answered.
type Proceed = Callable[[], None]


class RunList(ViewModel):
    """The latest runs as stored, newest first; the current run among them."""

    def __init__(self, executor: Executor, outlet: CloudOutlet) -> None:
        """Load through `executor`; `runs_changed`, `batches_changed` and the alert go to `outlet`."""
        super().__init__(executor)
        self._channel = LatestChannel(executor)
        self._outlet = outlet
        # The latest runs, newest first, as the last load answered.
        self.runs: tuple[RunProgress, ...] = ()
        # The error of the last load (None after one that succeeded).
        self.error: ErrorView | None = None
        # `proceed` of the start-up steps waiting for a load.
        self._waiting: list[Proceed] = []
        # Set while this model emits `runs_changed`, so its own reload is not sent twice.
        self._announcing = False
        # Set while this area emits `batches_changed` for its own run's change (the poll owes no check for it).
        self.announcing_end = False

    @property
    def current(self) -> RunProgress | None:
        """Return the run that is not final (newest first), else the last run, else None."""
        for progress in self.runs:
            if not is_final(progress):
                return progress
        return self.runs[0] if self.runs else None

    def run_active(self) -> bool:
        """Return whether a listed run is not final (the poll runs; the close question adds its cloud note)."""
        return any(not is_final(progress) for progress in self.runs)

    def build_list_request(self) -> ListRunsRequest:
        """Return the request of the run list."""
        return ListRunsRequest(action="list_runs", limit=RUN_HISTORY_LIMIT)

    def load(self, proceed: Proceed | None = None) -> None:
        """Load the runs (a load in flight is replaced); `proceed` (a start-up step's) is called once it answered."""
        if proceed is not None:
            self._waiting.append(proceed)
        self._channel.send(LIST_KEY, self.build_list_request(), self._listed)

    def on_runs_changed(self) -> None:
        """Reload after another part changed runs (this model's own announcement has loaded already)."""
        if not self._announcing:
            self.load()

    def announce(self) -> None:
        """Reload the runs, then tell the other pages that runs changed."""
        self.load()
        self._announcing = True
        # An `ExitStack` callback: the style checks refuse try/finally without `except` (their rule 501).
        with ExitStack() as reset:
            reset.callback(self._announced)
            self._outlet.emit(RUNS_CHANGED_SIGNAL)

    def _announced(self) -> None:
        self._announcing = False

    def _listed(self, response: Response) -> None:
        ended: tuple[RunProgress, ...] = ()
        if isinstance(response, ListRunsResponse):
            ended = self._ended(response.runs)
            self.runs = response.runs
            self.error = None
        elif isinstance(response, ErrorResponse) and response.error.code != ERROR_CANCELLED:
            self.error = error_view(response)
        for progress in ended:
            emit_ended(self, self._outlet, progress.run.covered_batch_ids)
        if ended:
            self._outlet.alert()
        # After the signal, which starts a stopped poll but owes it no check: the poll follows this
        # list next and stops again when no run is active and no results are awaited.
        self.changed()
        waiting = self._waiting
        self._waiting = []
        for proceed in waiting:
            proceed()

    def _ended(self, runs: tuple[RunProgress, ...]) -> tuple[RunProgress, ...]:
        """Return the runs that were not final in the last load and are final now."""
        open_before = {progress.run.run_id for progress in self.runs if not is_final(progress)}
        ended = [progress for progress in runs if is_final(progress)]
        return tuple(progress for progress in ended if progress.run.run_id in open_before)


def emit_ended(runs: RunList, outlet: CloudOutlet, covered: tuple[str, ...]) -> None:
    """Emit `batches_changed` for the jobs of a run this area launched, stopped or saw end (the poll owes no check for it).

    Qt reports an exception of a slot instead of raising it here, so the flag is always reset.
    """
    runs.announcing_end = True
    outlet.emit(BATCHES_CHANGED_SIGNAL, covered)
    runs.announcing_end = False
