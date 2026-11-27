"""Constants of the run-activity check and the launch scope (architecture §3.7)."""

import os
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

# Launch (plan m5 §1 "Run deadline and wait", "Cost estimate", "Pod create body"). The runtime and
# cost factors are placeholders until M7 measures a real run.

# GPU type of the worker pod (Runpod catalog id).
GPU_TYPE_ID: Final = "NVIDIA RTX A5000"
# GPUs per pod.
GPU_COUNT: Final = 1
# Lowest CUDA version the host must offer (excludes A5000 hosts that report CUDA 12.4).
MIN_CUDA_VERSION: Final = "12.8"
# Runpod cloud of the pod: on-demand secure cloud (the only one `queue` accepts).
POD_CLOUD: Final = "SECURE"
# Container disk of the pod, in GB.
POD_DISK_GB: Final = 60
# Where the Global Volume is mounted in the pod.
VOLUME_MOUNT_PATH: Final = "/workspace"
# Worker env variable: the run id (`transcribe_worker.pipeline.constants.ENV_RUN_ID`).
ENV_RUN_ID: Final = "TRANSCRIBE_RUN_ID"
# Worker env variable: seconds after which the worker's watchdog terminates the pod.
ENV_MAX_RUNTIME_S: Final = "MAX_RUNTIME_S"
# Worker env variable: keep the pod until its deadline after the work (test mode).
ENV_KEEP_POD_ALIVE: Final = "KEEP_POD_ALIVE"
# Env value of a true flag.
ENV_TRUE: Final = "true"
# Env value of a false flag.
ENV_FALSE: Final = "false"

# Shortest wait for a machine (queue `expiresAt` after the launch), in seconds (A19).
WAIT_MIN_S: Final = 300
# Longest wait for a machine (24 h; A19).
WAIT_MAX_S: Final = 86400
# Default wait for a machine (1 h, requirements "Runpod control").
WAIT_DEFAULT_S: Final = 3600
# Start-up allowance of `MAX_RUNTIME_S` (image pull, model load).
STARTUP_ALLOWANCE_S: Final = 1200
# Real-time factor of `MAX_RUNTIME_S` per second of audio (pessimistic).
RUNTIME_REAL_TIME_FACTOR: Final = 0.2
# Safety margin multiplied onto that factor.
RUNTIME_MARGIN: Final = 2
# Fixed overhead per pending job in `MAX_RUNTIME_S` (download, alignment, upload).
RUNTIME_JOB_OVERHEAD_S: Final = 60
# Weight of a `careful` job (two passes) in the runtime and the cost.
CAREFUL_WEIGHT: Final = 2
# Upper bound of `MAX_RUNTIME_S` (the worker caps it the same way).
MAX_RUNTIME_CAP_S: Final = 86400
# `MAX_RUNTIME_S` of a test-mode run (requirements "Deployed app").
TEST_MODE_RUNTIME_S: Final = 7200
# Seconds added after `MAX_RUNTIME_S` to the pod name deadline (queue `expiresAt` + runtime + this).
NAME_DEADLINE_MARGIN_S: Final = 600
# Start-up time of the low cost estimate.
COST_STARTUP_LOW_S: Final = 300
# Start-up time of the high cost estimate.
COST_STARTUP_HIGH_S: Final = 1200
# Real-time factor of the low cost estimate.
COST_FACTOR_LOW: Final = 0.05
# Real-time factor of the high cost estimate.
COST_FACTOR_HIGH: Final = 0.2
# Price per GPU hour used when the catalog cannot be read (USD, secure cloud, 2026-11-26).
FALLBACK_PRICE_PER_HOUR: Final = 0.27
# Decimals of the dollar amounts of the estimate.
COST_DECIMALS: Final = 2
# Seconds per hour.
SECONDS_PER_HOUR: Final = 3600

# Seconds after the pod request (`POST /pods`) before an empty name lookup may count; a pod
# missing from the lists earlier may be list lag.
LAUNCH_SETTLE_S: Final = 600
# Seconds between the two empty lookups (both after the settle time) that make a `launching`
# run `launch_failed`.
LAUNCH_LOOKUP_GAP_S: Final = 60
# Kind of a Runpod pod in activity sources, reconcile entries and error details.
POD_KIND: Final = "pod"
# Kind of a Runpod queued pod.
QUEUED_POD_KIND: Final = "queued_pod"
# Ids of pods and queued pods the requests accept (the reaper's rule).
RUNPOD_ID_PATTERN: Final = r"^[A-Za-z0-9_-]{1,64}$"

# Error code: no pending job in the launch scope, so no worker is started.
ERROR_NOTHING_TO_LAUNCH: Final = "nothing_to_launch"
# Error code: Runpod refused the pod request; nothing was created (details: the HTTP status).
ERROR_LAUNCH_REFUSED: Final = "launch_refused"
# Error code: the request names a run this database does not know.
ERROR_RUN_NOT_FOUND: Final = "run_not_found"
# Error code: the run has ended already (finished, failed or launch_failed).
ERROR_RUN_NOT_ACTIVE: Final = "run_not_active"
# Error code: the run's pod request has an unknown outcome and its pod is not listed yet.
ERROR_LAUNCH_UNRESOLVED: Final = "launch_unresolved"
# Error code: a terminate or cancel was sent but the follow-up read does not show it done.
ERROR_STOP_UNVERIFIED: Final = "stop_unverified"
# Error code: Runpod does not know the pod or queued pod.
ERROR_POD_NOT_FOUND: Final = "pod_not_found"
# Monitoring (`refresh_status`, plan m5 §1 "Run deadline and wait", "Run objects").

# Seconds added to `MAX_RUNTIME_S` after the first `RUNNING` sighting for the desktop deadline.
DESKTOP_DEADLINE_MARGIN_S: Final = 600
# Worker state while the worker transcribes (the stage the app shows).
WORKER_TRANSCRIBING: Final = "transcribing"
# Worker state of a worker that is starting up (image pulled, model loading).
WORKER_STARTING: Final = "starting"
# Worker state of a worker that finished every job it could.
WORKER_FINISHED: Final = "finished"
# Worker state of a worker that failed.
WORKER_FAILED: Final = "failed"
# Batch phase whose `_done.json` and job results `refresh_status` looks for.
READY_PHASE: Final = "ready"
# Batch phase a `ready` batch moves to once its `_done.json` is there.
TRANSCRIBED_PHASE: Final = "transcribed"
# Job status of a transcribe job whose result reports a failure (or could not be read in time).
JOB_FAILED: Final = "failed"
# Failure reason of a job whose result JSON could not be read once its batch had `_done.json`.
UNREADABLE_RESULT_ERROR: Final = "unreadable result"
# Failure reason of a job that has no result JSON although its batch has `_done.json`.
MISSING_RESULT_ERROR: Final = "missing result"
# Kind of the jobs the worker runs.
TRANSCRIBE_KIND: Final = "transcribe"
# Suffix of a job's result JSON (its "done" marker).
RESULT_SUFFIX: Final = ".json"
# Name of a run's worker log in the user log dir, kept after `runs/<run_id>/` is deleted.
WORKER_LOG_NAME: Final = "worker-{run_id}.log"
# Name of a further worker log of the run (collected again after a straggler; first number 2).
WORKER_LOG_EXTRA_NAME: Final = "worker-{run_id}-{number}.log"
# Number of the first further worker log of a run.
FIRST_EXTRA_LOG_NUMBER: Final = 2
# Prefix of the temp file a worker log is written to before it is published (by run id).
WORKER_LOG_TEMP_PREFIX: Final = ".worker-{run_id}-"
# Suffix of that temp file (never matches a kept log's name).
WORKER_LOG_TEMP_SUFFIX: Final = ".part"
# Permissions of a worker log created without a hard link (POSIX; like the temp file's).
WORKER_LOG_FILE_MODE: Final = 0o600
# Flags of that exclusive create for writing: it fails with `FileExistsError` on a taken name.
WORKER_LOG_CREATE_FLAGS: Final = os.O_CREAT | os.O_EXCL | os.O_WRONLY
# Decimals of the cost so far (dollars).
COST_SO_FAR_DECIMALS: Final = 2

# Error code: the pod or queued pod is not one of the app's (`transcribe-ui-*`).
ERROR_NOT_APP_POD: Final = "not_an_app_pod"
# Error code: a cloud cleanup of a batch was interrupted, so no worker may start until it is repeated.
ERROR_CLEANUP_PENDING: Final = "cleanup_pending"
# JSON path of a batch's interrupted cleanup in its batch payload (its `cleanup_pending`).
CLEANUP_PENDING_PATH: Final = "$.cleanup_pending"
