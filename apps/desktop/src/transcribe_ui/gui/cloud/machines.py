"""View-model of the cloud machines list: the start-up check, Check, Terminate and Cancel (§3.1 "GUI").

Qt-free. `reconcile_cloud` lists the app's live pods and queued pods with their role (the
current run's, or a straggler) and whether they are past their time limit; its `attention`
puts a link to this list into the status bar and a banner on the Cloud page. A straggler or an
overdue machine gets its stop (`terminate_pod` for a pod, `cancel_queued_pod` for a queued
pod), which the backend asks to confirm first. A check also resolves `launching` runs, so the
run list reloads after it. A failed check shows as a banner with Retry, never as a dialog.
"""

from collections.abc import Callable

from transcribe_ui.api.classes import ErrorResponse, Response
from transcribe_ui.api.constants import ERROR_CANCELLED
from transcribe_ui.gui.cloud.commands import CloudCommands
from transcribe_ui.gui.cloud.constants import RECONCILE_KEY
from transcribe_ui.gui.cloud.plan_display import MachineRow, machine_row
from transcribe_ui.gui.cloud.ports import CloudOutlet
from transcribe_ui.gui.executor import ExclusiveChannel, Executor
from transcribe_ui.gui.feedback import ErrorView, error_view
from transcribe_ui.gui.viewmodel import ViewModel
from transcribe_ui.runs.classes import CancelQueuedPodRequest, ReconcileCloudRequest, TerminatePodRequest
from transcribe_ui.runs.responses import ReconcileCloudResponse

# Called once a start-up step's request answered.
type Proceed = Callable[[], None]


class MachinesModel(ViewModel):
    """The app's live cloud machines, and the stop of a straggler or an overdue one."""

    def __init__(self, executor: Executor, outlet: CloudOutlet, runs_changed: Callable[[], None]) -> None:
        """Send through `executor`; confirmations and errors go to `outlet`; `runs_changed` after a check or stop."""
        super().__init__(executor)
        self._channel = ExclusiveChannel(executor)
        self._commands = CloudCommands(executor, outlet, self.changed)
        self._runs_changed = runs_changed
        self.rows: tuple[MachineRow, ...] = ()
        # Whether a machine is a straggler or past its time limit.
        self.attention = False
        # Whether a check answered (the list is known).
        self.checked = False
        # The error of the last check (None after one that succeeded).
        self.error: ErrorView | None = None
        self._waiting: list[Proceed] = []

    @property
    def checking(self) -> bool:
        """Return whether a check is in flight."""
        return self._channel.busy(RECONCILE_KEY)

    def stopping(self, row: MachineRow) -> bool:
        """Return whether the stop of a machine is in flight."""
        return self._commands.busy(row.entry.entry_id)

    def check(self, proceed: Proceed | None = None) -> None:
        """Check the cloud machines (a check in flight answers for both); `proceed` once it answered."""
        if proceed is not None:
            self._waiting.append(proceed)
        request = ReconcileCloudRequest(action="reconcile_cloud")
        sent = self._channel.send(RECONCILE_KEY, request, self._checked, None, self.changed)
        if sent is not None:
            self.changed()

    def stop(self, row: MachineRow) -> None:
        """Terminate a straggler or overdue pod, or cancel such a queued pod, through the backend's confirmation."""
        if row.action_label is None:
            return
        entry = row.entry
        request: TerminatePodRequest | CancelQueuedPodRequest
        if entry.kind == "pod":
            request = TerminatePodRequest(action="terminate_pod", pod_id=entry.entry_id)
        else:
            request = CancelQueuedPodRequest(action="cancel_queued_pod", queued_pod_id=entry.entry_id)
        if self._commands.send(entry.entry_id, request, self._stopped):
            self.changed()

    def _checked(self, response: Response) -> None:
        if isinstance(response, ReconcileCloudResponse):
            self.rows = tuple(machine_row(entry) for entry in response.entries)
            self.attention = response.attention
            self.checked = True
            self.error = None
            self._runs_changed()
        elif isinstance(response, ErrorResponse) and response.error.code != ERROR_CANCELLED:
            self.error = error_view(response)
        self.changed()
        waiting = self._waiting
        self._waiting = []
        for proceed in waiting:
            proceed()

    def _stopped(self, _response: Response) -> None:
        self._runs_changed()
        self.check()
