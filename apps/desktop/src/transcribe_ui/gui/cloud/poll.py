"""The checks of the cloud: the poll while a run is not final, Refresh now, the start-up check (§3.1 "GUI").

Qt-free. While the run list holds a run that is not final (or right after a launch), the page
sends `refresh_status` every `REFRESH_INTERVAL_S` on a single-flight channel: a tick is skipped
while the previous one is queued or running, and the timer stops once the list holds only
final runs. A check's `runs_busy`, `state_busy` or `cloud_unreachable` shows as "retrying" (the
next tick tries again), never as an error dialog; another error shows in the page. Each check
reloads the run list (what the page shows), even a failed one, and reloads the jobs it reported.
The timer is injected, so the poll's timing is tested without sleeping.
"""

from collections.abc import Callable

from transcribe_ui.api.classes import ErrorResponse, Response
from transcribe_ui.api.constants import ERROR_CANCELLED
from transcribe_ui.gui.cloud.constants import BATCHES_CHANGED_SIGNAL, REFRESH_INTERVAL_MS, RETRYING_CODES
from transcribe_ui.gui.cloud.display import local_time
from transcribe_ui.gui.cloud.ports import CloudOutlet, TimerFactory
from transcribe_ui.gui.cloud.runs import RunList
from transcribe_ui.gui.constants import CALL_QUEUED, CALL_RUNNING
from transcribe_ui.gui.executor import Executor, SingleFlightChannel
from transcribe_ui.gui.feedback import ErrorView, error_view
from transcribe_ui.gui.viewmodel import ViewModel
from transcribe_ui.i18n.catalog import tr
from transcribe_ui.runs.classes import RefreshStatusRequest
from transcribe_ui.runs.responses import RefreshStatusResponse

# Called once a start-up step's request answered.
type Proceed = Callable[[], None]


class CloudPoll(ViewModel):
    """The checks of the cloud (`refresh_status`): the poll, Refresh now and the start-up check."""

    def __init__(self, executor: Executor, outlet: CloudOutlet, runs: RunList, timer: TimerFactory) -> None:
        """Check through `executor`; follow `runs` (poll while one is not final) on `timer`."""
        super().__init__(executor)
        self._channel = SingleFlightChannel(executor)
        self._outlet = outlet
        self._runs = runs
        self._timer = timer(REFRESH_INTERVAL_MS, self._timed)
        # The error of the last check (None after one that succeeded).
        self.error: ErrorView | None = None
        # `proceed` of the start-up step waiting for the check in flight.
        self._waiting: list[Proceed] = []
        runs.subscribe(self.follow)

    @property
    def checking(self) -> bool:
        """Return whether a check is queued or running."""
        return self._channel.state is not None

    def follow(self) -> None:
        """Poll while a listed run is not final; stop once every run is final."""
        if not self._runs.run_active():
            self._timer.stop()
        elif not self._timer.active:
            self._timer.start()

    def start(self) -> None:
        """Poll from now on (a run was launched; its list entry may not be loaded yet)."""
        if not self._timer.active:
            self._timer.start()

    def tick(self, proceed: Proceed | None = None) -> bool:
        """Check the cloud unless the previous check is still queued or running; return whether one was sent."""
        if self.checking:
            return False
        if proceed is not None:
            # Before the send: an executor may answer inside it.
            self._waiting.append(proceed)
        request = RefreshStatusRequest(action="refresh_status")
        self._channel.send(request, self._answered, self.changed)
        self.changed()
        return True

    def refresh_now(self) -> None:
        """Check the cloud now ("Refresh now"); the next automatic check comes a whole interval later."""
        if self.tick() and self._timer.active:
            self._timer.start()

    def note(self) -> str:
        """Return what the page says about the checks: one running or waiting, or the last one retrying."""
        state = self._channel.state
        if state == CALL_RUNNING:
            return tr("Checking the cloud...")
        if state == CALL_QUEUED:
            return tr("Waiting to check the cloud...")
        if self.error is None or self.error.code not in RETRYING_CODES:
            return ""
        current = self._runs.current
        refreshed = None if current is None else current.refreshed_at
        if refreshed is None:
            return tr("The cloud could not be checked; retrying at the next check.")
        shown = local_time(refreshed, seconds=True)
        return tr("Last updated {time}; retrying at the next check.").format(time=shown)

    def shown_error(self) -> ErrorView | None:
        """Return the last check's error unless the next check simply retries it."""
        error = self.error
        return None if error is None or error.code in RETRYING_CODES else error

    def _timed(self) -> None:
        self.tick()

    def _answered(self, response: Response) -> None:
        reported: tuple[str, ...] = ()
        if isinstance(response, RefreshStatusResponse):
            self.error = None
            found = (*(batch.batch_id for batch in response.batches), *response.published)
            reported = tuple(dict.fromkeys(found))
        elif isinstance(response, ErrorResponse) and response.error.code != ERROR_CANCELLED:
            self.error = error_view(response)
        # Even a failed check may have changed a run before it failed: the list shows what is stored.
        self._runs.announce()
        if reported:
            self._outlet.emit(BATCHES_CHANGED_SIGNAL, reported)
        self.changed()
        waiting = self._waiting
        self._waiting = []
        for proceed in waiting:
            proceed()
