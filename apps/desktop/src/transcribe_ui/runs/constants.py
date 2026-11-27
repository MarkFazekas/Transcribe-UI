"""Constants of the run-activity check and the launch scope (architecture §3.7)."""

from typing import Final

from transcribe_ui.runpod.constants import QUEUED_POD_CANCELLED, QUEUED_POD_EXPIRED, QUEUED_POD_FAILED

# Local run statuses after which the run can no longer start or run a worker; any other status
# (`launching`, `queued`, `running`, or one this version does not know) counts as active.
FINAL_RUN_STATUSES: Final = frozenset(("finished", "failed", "cancelled", "launch_failed"))
# Queued-pod statuses that never lead to a pod; every other status counts as active (fail closed).
IGNORED_QUEUED_POD_STATUSES: Final = frozenset((QUEUED_POD_FAILED, QUEUED_POD_EXPIRED, QUEUED_POD_CANCELLED))
# Worker run states after which the worker writes nothing more (`runs/<run_id>/status.json`).
FINAL_WORKER_STATES: Final = frozenset(("finished", "failed"))
# Seconds after its `updated_at` a non-final `status.json` still counts as a live worker; the
# worker writes it every ~30 s, and a stuck upload may take a few minutes of retries.
HEARTBEAT_STALE_S: Final = 600
# Seconds of clock difference (between R2 and this computer) and of upload delay tolerated on
# top of `HEARTBEAT_STALE_S`: a heartbeat object written, or updated, longer ago than both is
# never active, and an `updated_at` is never taken as later than the run's deadline plus this.
HEARTBEAT_MARGIN_S: Final = 300
# Seconds after a queued pod's deploy (`resolvedAt`) by which its pod must show in the pod list;
# before that, a pod missing from the list (or a 404 for it) may be list lag and counts as active.
DEPLOY_SETTLE_S: Final = 600
# Object name the heartbeat keys end with, below `runs/<run_id>`.
STATUS_KEY_SUFFIX: Final = "/status.json"
# Number of decimals of the total pending duration in the launch scope fingerprint.
FINGERPRINT_DURATION_DECIMALS: Final = 3
# Worker state recorded for a heartbeat object that cannot be read (it counts as active).
UNREADABLE_STATE: Final = "unreadable"

# Error code: a worker run is active, so the request was refused; nothing was changed.
ERROR_RUN_ACTIVE: Final = "run_active"
