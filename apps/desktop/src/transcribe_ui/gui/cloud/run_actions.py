"""Stop and Cancel of the current run, and following a run the start dialog launched (Q34; §3.1 "GUI").

Qt-free. "Stop waiting" (Q34's Cancel, while waiting for a machine) and "Stop" (starting or transcribing) both send
`cancel_worker`, which the backend asks to confirm first: the run and its stage, and that the
subtitles already finished are kept while the files not done yet wait for the next start. Stop
runs on the short pool, so it is never queued behind an upload or a download. Closing the
window never stops a run.
"""

from functools import partial

from transcribe_ui.api.classes import Response
from transcribe_ui.gui.cloud.commands import CloudCommands
from transcribe_ui.gui.cloud.constants import BATCHES_CHANGED_SIGNAL
from transcribe_ui.gui.cloud.display import is_final
from transcribe_ui.gui.cloud.poll import CloudPoll
from transcribe_ui.gui.cloud.ports import CloudOutlet
from transcribe_ui.gui.cloud.runs import RunList
from transcribe_ui.gui.executor import Executor
from transcribe_ui.gui.viewmodel import ViewModel
from transcribe_ui.runs.classes import CancelWorkerRequest
from transcribe_ui.runs.responses import CancelWorkerResponse, LaunchWorkerResponse


class RunActions(ViewModel):
    """Stop or Cancel of the current run; following a launched run."""

    def __init__(self, executor: Executor, outlet: CloudOutlet, runs: RunList, poll: CloudPoll) -> None:
        """Send through `executor`; confirmations, errors and signals go to `outlet`."""
        super().__init__(executor)
        self._commands = CloudCommands(executor, outlet, self.changed)
        self._outlet = outlet
        self._runs = runs
        self._poll = poll

    @property
    def stopping(self) -> bool:
        """Return whether a stop of the current run is in flight."""
        current = self._runs.current
        return current is not None and self._commands.busy(current.run.run_id)

    def stop(self) -> None:
        """Stop (or cancel the wait of) the current run, through the backend's confirmation (Q34)."""
        current = self._runs.current
        if current is None or is_final(current):
            return
        run = current.run
        request = CancelWorkerRequest(action="cancel_worker", run_id=run.run_id)
        stopped = partial(self._stopped, run.covered_batch_ids)
        if self._commands.send(run.run_id, request, stopped):
            self.changed()

    def launched(self, response: LaunchWorkerResponse) -> None:
        """Follow a run the start dialog launched: poll from now on, show it, reload the jobs it covers."""
        self._poll.start()
        self._runs.announce()
        covered = (*response.run.covered_batch_ids, *response.published)
        self._outlet.emit(BATCHES_CHANGED_SIGNAL, tuple(dict.fromkeys(covered)))

    def _stopped(self, covered: tuple[str, ...], response: Response) -> None:
        # A failed stop may have recorded a pod or ended the run before it failed: reload either way.
        self._runs.announce()
        if isinstance(response, CancelWorkerResponse):
            self._outlet.emit(BATCHES_CHANGED_SIGNAL, covered)
