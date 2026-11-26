"""Constants of the worker entry (PID 1) and its watchdog."""

from typing import Final

# Runpod REST API v2 base URL used for self-termination.
DEFAULT_API_BASE_URL: Final = "https://api.runpod.io/v2"
# Environment variable overriding the API base URL (tests point it at a fake server).
ENV_API_BASE_URL: Final = "RUNPOD_API_BASE_URL"
# Environment variable with the pod's own id, set by Runpod.
ENV_POD_ID: Final = "RUNPOD_POD_ID"
# Environment variable with the pod-scoped API key, set by Runpod (or a pods-only key, A15).
ENV_API_KEY: Final = "RUNPOD_API_KEY"
# Environment variable: "true" keeps the pod after the worker ends (test mode, A6).
ENV_KEEP_POD_ALIVE: Final = "KEEP_POD_ALIVE"
# Environment variable: seconds after start when the watchdog terminates the pod.
ENV_MAX_RUNTIME_S: Final = "MAX_RUNTIME_S"

# Watchdog deadline when MAX_RUNTIME_S is missing or invalid (the test-mode cap, A6).
DEFAULT_MAX_RUNTIME_S: Final = 2 * 60 * 60
# Upper bound for MAX_RUNTIME_S, so a typo cannot keep a pod alive for days.
MAX_RUNTIME_CEILING_S: Final = 24 * 60 * 60
# Values of KEEP_POD_ALIVE that mean "yes" (compared case-insensitively).
TRUE_VALUES: Final = frozenset(("1", "true", "yes", "on"))

# First wait after a failed terminate attempt; doubled after each failure.
RETRY_INITIAL_DELAY_S: Final = 5.0
# Longest wait between terminate attempts.
RETRY_MAX_DELAY_S: Final = 60.0
# Failed terminate attempts after which every further attempt also asks for "stop".
STOP_AFTER_FAILURES: Final = 3
# Host suffix an https API base URL must have (the Authorization header goes there).
API_HOST_SUFFIX: Final = ".runpod.io"
# Only host allowed over plain http: a fake API on localhost in tests.
LOCAL_TEST_HOST: Final = "127.0.0.1"
# Timeout of one Runpod API request.
HTTP_TIMEOUT_S: Final = 30.0
# Pod status meaning the pod no longer exists.
TERMINATED_STATUS: Final = "TERMINATED"
# HTTP status of a pod that does not exist (any more).
HTTP_NOT_FOUND: Final = 404

# Module run as the worker child process.
CHILD_MODULE: Final = "transcribe_worker.run"
# Module run as the independent watchdog process.
WATCHDOG_MODULE: Final = "transcribe_worker.entry.watchdog"
# Seconds between checks while the entry waits in test mode (deadline, watchdog alive).
KEEP_ALIVE_POLL_S: Final = 30.0
# Seconds between log lines while a misconfigured entry waits to be killed from outside.
MISCONFIGURED_SLEEP_S: Final = 600.0
# Exit code recorded when the worker child is killed at the deadline (as `timeout` does).
CHILD_TIMEOUT_EXIT_CODE: Final = 124
# Exit code of the entry when it is asked to stop by a signal (128 + SIGTERM).
SIGTERM_EXIT_CODE: Final = 143
# Log line format of the entry and the watchdog.
LOG_FORMAT: Final = "%(asctime)s %(levelname)s %(name)s: %(message)s"
