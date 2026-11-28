"""The confirmation `cancel_worker` asks first (Q34): the run, its stage, and what stopping keeps.

Reads only the database (no lock, no Runpod call), so the question shows at once even while a
check holds the runs lock; the confirmed request checks everything again under the lock.
"""

from typing import TYPE_CHECKING

from transcribe_ui.api.classes import Confirmation, NeedsConfirmation
from transcribe_ui.runs.classes import CancelWorkerRequest
from transcribe_ui.runs.constants import CONFIRM_STOP_FIELD, CONFIRMATION_CANCEL_WORKER, FINAL_RUN_STATUSES
from transcribe_ui.runs.errors import RunNotActiveError
from transcribe_ui.runs.progress import runs_progress
from transcribe_ui.runs.responses import CancelWorkerResponse
from transcribe_ui.state.store import LocalState

if TYPE_CHECKING:
    from pydantic import JsonValue


def cancel_asked(state: LocalState, request: CancelWorkerRequest) -> CancelWorkerResponse | NeedsConfirmation:
    """Return the confirmation of stopping a run that is not final (a cancelled one is answered as done).

    Raises:
        RunNotFoundError: The database does not know the run.
        RunNotActiveError: The run has ended otherwise.
    """
    shown = runs_progress(state, (request.run_id,))[0]
    run = shown.run
    if run.status == "cancelled":
        return CancelWorkerResponse(run=run)
    if run.status in FINAL_RUN_STATUSES:
        raise RunNotActiveError(run.run_id, run.status)
    details: dict[str, JsonValue] = {"run_id": run.run_id, "stage": shown.stage, "run_status": run.status}
    message = (
        f"Stop run {run.run_id} ({shown.stage})? Subtitles already finished are kept; the files not done yet "
        "wait for the next start of transcription."
    )
    confirmation = Confirmation(
        code=CONFIRMATION_CANCEL_WORKER,
        field=CONFIRM_STOP_FIELD,
        set_to=True,
        message=message,
        details=details,
    )
    return NeedsConfirmation(action=request.action, confirmations=(confirmation,))
