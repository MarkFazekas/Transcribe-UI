"""Constants of the external pod reaper."""

from datetime import timedelta
from typing import Final

# Environment variable with the pods-only Runpod key of the reaper.
ENV_API_KEY: Final = "RUNPOD_REAPER_API_KEY"
# Environment variable overriding the API base URL (tests point it at a fake server).
ENV_API_BASE_URL: Final = "RUNPOD_REAPER_API_BASE_URL"

# Time after a pod's deadline before the reaper acts, so it does not race the in-pod layers.
GRACE: Final = timedelta(minutes=10)
# The pass stops starting new actions after this long (the next scheduled run continues).
PASS_BUDGET: Final = timedelta(minutes=4)

# Attempts of one API call within a pass (the next scheduled run retries anyway).
CALL_ATTEMPTS: Final = 3
# Wait after the first failed attempt of a call; doubled after each further failure.
RETRY_INITIAL_DELAY_S: Final = 2.0
# Checks that a terminated pod or cancelled queued pod is really gone.
CONFIRM_CHECKS: Final = 4
# Wait between two such checks.
CONFIRM_DELAY_S: Final = 5.0
# Most pages read from one list endpoint (1000 items each); more means something is wrong.
MAX_PAGES: Final = 20

# API path of the pod list and of one pod (`/pods/{id}`).
PODS_PATH: Final = "/pods"
# API path of the queued-pod list and of one queued pod (`/queued-pods/{id}`).
QUEUED_PODS_PATH: Final = "/queued-pods"
# Query parameter carrying the pagination cursor.
CURSOR_PARAMETER: Final = "cursor"
# Status of a queued pod that still waits for capacity (every other status is final).
QUEUED_STATUS: Final = "QUEUED"
# Longest part of a malformed pod name written to the (public) Actions log.
LOGGED_NAME_MAX_LENGTH: Final = 100

# Exit code of a pass that found nothing wrong.
EXIT_OK: Final = 0
# Exit code of a pass with a failure or a malformed app pod name (GitHub then notifies).
EXIT_FAILED: Final = 1
# Log line format (the Actions log adds its own timestamps, but local runs need them).
LOG_FORMAT: Final = "%(asctime)s %(levelname)s %(message)s"
