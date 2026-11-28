"""Constants of the Cloud page, the start dialog and the status bar (architecture §3.1 "GUI")."""

from typing import Final, Literal

from transcribe_ui.batches.responses import BatchStage
from transcribe_ui.runpod.constants import ERROR_CLOUD_UNREACHABLE
from transcribe_ui.state.constants import ERROR_RUNS_BUSY, ERROR_STATE_BUSY

# Where a status bar link leads: the Cloud page, its cloud machines list, or a job.
type StatusTarget = Literal["cloud", "machines", "job"]
# Where the start dialog is: closed, planning, showing a plan, starting the machine, or done.
type StartPhase = Literal["closed", "planning", "ready", "launching", "launched"]
# A request of the start dialog that can fail: the plan or the launch.
type StartStep = Literal["plan", "launch"]

# Seconds between two checks of a run that is not final (requirements "Runpod control": every 5 minutes).
REFRESH_INTERVAL_S: Final = 300
# The same in milliseconds, for the timer.
REFRESH_INTERVAL_MS: Final = REFRESH_INTERVAL_S * 1000
# Milliseconds between two repaints of the remaining-time countdown.
COUNTDOWN_TICK_MS: Final = 1000
# Runs the history shows (`list_runs`' default).
RUN_HISTORY_LIMIT: Final = 20
# The waits for a machine the start dialog offers, in seconds; only those within the bounds the backend gives show.
WAIT_CHOICES_S: Final = (300, 900, 1800, 3600, 7200, 14400, 28800, 43200, 86400)
# Error codes of a check that are shown as "retrying" (the next check tries again), never as an error dialog.
RETRYING_CODES: Final = frozenset((ERROR_RUNS_BUSY, ERROR_STATE_BUSY, ERROR_CLOUD_UNREACHABLE))
# Credential whose presence lets the app start, follow and stop cloud machines (`credentials_status`).
RUNPOD_KEY_NAME: Final = "runpod_api_key"
# Credential source of a credential that is not set.
CREDENTIAL_MISSING: Final = "missing"
# Details key of the launch confirmation that holds the current plan.
PLAN_DETAIL: Final = "plan"
# Channel key of the run list's loads.
LIST_KEY: Final = "runs"
# Channel key of the launch plan's loads.
PLAN_KEY: Final = "plan"
# Channel key of the launch.
LAUNCH_KEY: Final = "launch"
# Channel key of the cloud machines check.
RECONCILE_KEY: Final = "reconcile"
# Channel key of the start-up check of the credentials.
CREDENTIALS_KEY: Final = "credentials"
# Bus signal: batches changed (a tuple of batch ids; each area names the bus signals it emits, as jobs/ and selection/ do).
BATCHES_CHANGED_SIGNAL: Final = "batches_changed"
# Bus signal: runs changed.
RUNS_CHANGED_SIGNAL: Final = "runs_changed"
# Bus signal: delete an unknown batch's cloud data (`batch_id`).
DELETE_UNKNOWN_SIGNAL: Final = "delete_unknown_batch"
# Bus signal: open the cloud machines list.
OPEN_MACHINES_SIGNAL: Final = "open_cloud_machines"
# Separates a status bar link's target from its argument (`job:<batch id>`).
LINK_SEPARATOR: Final = ":"
# The stage the step header of the start dialog shows (step 3 of a job).
TRANSCRIPTION_STAGE: Final[BatchStage] = "transcription"
# Separator of the status bar's items.
STATUS_SEPARATOR: Final = " · "
# Scale of the progress bar (the fraction times this).
PROGRESS_SCALE: Final = 1000
# Decimals of a cost in USD.
COST_DECIMALS: Final = 2
