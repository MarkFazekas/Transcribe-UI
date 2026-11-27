"""Constants of the external pod reaper."""

from datetime import timedelta
from typing import Final

from transcribe_worker.entry.constants import MAX_RUNTIME_CEILING_S

# Environment variable with the pods-only Runpod key of the reaper.
ENV_API_KEY: Final = "RUNPOD_REAPER_API_KEY"
# Environment variable overriding the API base URL (tests point it at a fake server).
ENV_API_BASE_URL: Final = "RUNPOD_REAPER_API_BASE_URL"

# Time after a pod's deadline before the reaper acts, so it does not race the in-pod layers.
GRACE: Final = timedelta(minutes=10)
# Longest queue wait Runpod allows, in days (`queue.expiresAt`, research/runpod.md §2).
RUNPOD_MAX_QUEUE_WAIT_DAYS: Final = 30
# Longest queue wait Runpod allows.
RUNPOD_MAX_QUEUE_WAIT: Final = timedelta(days=RUNPOD_MAX_QUEUE_WAIT_DAYS)
# Longest plausible span from creation to deadline (queue wait + runtime ceiling + a day of margins).
MAX_DEADLINE_SPAN: Final = RUNPOD_MAX_QUEUE_WAIT + timedelta(seconds=MAX_RUNTIME_CEILING_S) + timedelta(days=1)
# Time budgets. The hard bound is the workflow's 15-minute job timeout; the figures below
# assume one-page lists and that the 30 s HTTP timeout bounds every operation (it is a socket
# timeout, so a trickling response or a slow DNS lookup can take longer). A list then takes
# 3 x 30 + 2 + 4 = 96 s; an action takes 96 s (DELETE with retries) + 4 x 30 + 3 x 5 = 135 s
# (confirmation) = 231 s. The last cancel starts by 60 s and ends by 291 s; the pod phase then
# starts no terminate after max(240 s, 291 + 120 s) = 411 s, and the last ends by 642 s
# (10.7 min), leaving room for checkout and install.
# The queued phase starts no new cancel after this long from the pass start (its list included;
# a slow list fails the pass loudly, and a queued pod that deploys is caught by the pod phase).
QUEUED_PHASE_BUDGET: Final = timedelta(minutes=1)
# The pod phase starts no new terminate after this long from the pass start ...
PASS_BUDGET: Final = timedelta(minutes=4)
# ... or after this long from its own start (list included), whichever is later.
POD_PHASE_MIN_WINDOW: Final = timedelta(minutes=2)

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
# Accepted ids of app pods / queued pods (full match); others are left alone (ids go into paths).
ITEM_ID_PATTERN: Final = r"[A-Za-z0-9_-]{1,64}"
# Longest part of an untrusted value (malformed name or id) written, escaped, to the Actions log.
LOGGED_VALUE_MAX_LENGTH: Final = 100

# Exit code of a pass that found nothing wrong.
EXIT_OK: Final = 0
# Exit code of a pass with a failure or a malformed app pod name (GitHub then notifies).
EXIT_FAILED: Final = 1
# Log line format (the Actions log adds its own timestamps, but local runs need them).
LOG_FORMAT: Final = "%(asctime)s %(levelname)s %(message)s"
