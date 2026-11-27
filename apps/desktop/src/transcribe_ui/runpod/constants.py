"""Constants of the Runpod client and the cloud setup (architecture §3.6-3.7)."""

from typing import Final

import httpx

from transcribe_contracts.constants import SCHEMA_MAJOR

# Base URL of the Runpod REST API, version 2 (the first version was retired on 2026-11-15).
API_BASE_URL: Final = "https://api.runpod.io/v2"
# The only URL scheme the API key is sent over.
API_SCHEME: Final = "https"
# The only host the API key is sent to (exact match, no port; REST v2 lives on it alone).
API_HOST: Final = "api.runpod.io"

# Seconds to wait for a connection to the API.
CONNECT_TIMEOUT_S: Final = 10.0
# Seconds to wait for a response, between bytes, and for sending the body.
READ_TIMEOUT_S: Final = 30.0
# Attempts per call (the first included) for a failure that may be retried.
MAX_ATTEMPTS: Final = 5
# Delay before the first retry; doubled for each further one.
RETRY_INITIAL_DELAY_S: Final = 1.0
# Upper bound of the backoff delay between two attempts.
RETRY_MAX_DELAY_S: Final = 30.0
# Upper bound of a 429 `Retry-After` the client honours (a longer one is cut to this).
RETRY_AFTER_MAX_S: Final = 60.0
# Attempts of the credential check (short, like the R2 check, so a hanging API reports soon).
CHECK_ATTEMPTS: Final = 2
# Connect timeout of the credential check.
CHECK_CONNECT_TIMEOUT_S: Final = 5.0
# Read timeout of the credential check.
CHECK_READ_TIMEOUT_S: Final = 10.0
# Longest wait between the credential check's attempts (a 429 included), so it reports
# within about 30 s: 2 x (5 s connect + 10 s read) + this.
CHECK_RETRY_WAIT_MAX_S: Final = 1.0
# Message of a passed Runpod credential check.
RUNPOD_CHECK_PASSED: Final = "Runpod accepted the API key and the pod list can be read."
# Longest single sleep while waiting to retry, so a cancel is noticed within this time.
WAIT_SLICE_S: Final = 0.5
# Transport failures raised before anything was sent: a create may be retried after them.
NOT_SENT_ERRORS: Final = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
# Pages followed per list call at most (1000 entries per page).
MAX_PAGES: Final = 100
# Query parameter of the opaque pagination cursor.
CURSOR_PARAMETER: Final = "cursor"
# Query parameter of the page size.
LIMIT_PARAMETER: Final = "limit"

# HTTP status: a create with `queue` answered with a queued pod (no capacity yet).
HTTP_ACCEPTED: Final = 202
# HTTP status: not found (a single object: it does not exist or is gone).
HTTP_NOT_FOUND: Final = 404
# HTTP status: conflict (a secret name taken; a global volume still mounted).
HTTP_CONFLICT: Final = 409
# HTTP status: rate limited; `Retry-After` says when to try again.
HTTP_TOO_MANY_REQUESTS: Final = 429
# HTTP status: the key is missing, malformed, expired or invalid.
HTTP_UNAUTHORIZED: Final = 401
# HTTP status: the key is valid but lacks the scope for the call.
HTTP_FORBIDDEN: Final = 403
# Lowest server-error status (500 and above).
HTTP_SERVER_ERROR: Final = 500
# Lowest client-error status; redirects lie below it and are never followed.
HTTP_CLIENT_ERROR: Final = 400
# Lowest status that is not a success.
HTTP_FIRST_NON_SUCCESS: Final = 300

# API path of pods (below the base URL).
PODS_PATH: Final = "/pods"
# API path of queued pods ("deploy when available").
QUEUED_PODS_PATH: Final = "/queued-pods"
# API path of the account's templates.
TEMPLATES_PATH: Final = "/templates"
# API path of the account's secrets.
SECRETS_PATH: Final = "/account/secrets"
# API path of the account's Global Volumes.
GLOBAL_VOLUMES_PATH: Final = "/global-volumes"
# API path of the GPU type catalog (prices).
GPU_CATALOG_PATH: Final = "/catalog/gpus"

# API key scope: list and read pods and queued pods (OpenAPI `security`; architecture §3.6).
SCOPE_PODS_READ: Final = "pods:read"
# API key scope: create and terminate pods, cancel queued pods.
SCOPE_PODS_WRITE: Final = "pods:write"
# API key scope: list templates.
SCOPE_TEMPLATES_READ: Final = "templates:read"
# API key scope: create, update and delete templates.
SCOPE_TEMPLATES_WRITE: Final = "templates:write"
# API key scope: list secrets (names only; values are write-only).
SCOPE_SECRETS_READ: Final = "secrets:read"
# API key scope: create, update and delete secrets.
SCOPE_SECRETS_WRITE: Final = "secrets:write"
# API key scope: list Global Volumes.
SCOPE_VOLUMES_READ: Final = "volumes:read"
# API key scope: create and delete Global Volumes.
SCOPE_VOLUMES_WRITE: Final = "volumes:write"
# API key scope: read the GPU catalog (prices).
SCOPE_CATALOG_READ: Final = "catalog:read"
# Every scope the desktop's Runpod key needs, in the order §3.6 lists them.
REQUIRED_SCOPES: Final = (
    SCOPE_PODS_READ,
    SCOPE_PODS_WRITE,
    SCOPE_TEMPLATES_READ,
    SCOPE_TEMPLATES_WRITE,
    SCOPE_SECRETS_READ,
    SCOPE_SECRETS_WRITE,
    SCOPE_VOLUMES_READ,
    SCOPE_VOLUMES_WRITE,
    SCOPE_CATALOG_READ,
)

# Pod status of a terminated pod (the only status in which a pod no longer exists).
POD_TERMINATED: Final = "TERMINATED"
# Queued-pod status while it still waits for capacity.
QUEUED_POD_QUEUED: Final = "QUEUED"
# Queued-pod status once its pod was created (`podId` set).
QUEUED_POD_DEPLOYED: Final = "DEPLOYED"
# Queued-pod status after a cancel.
QUEUED_POD_CANCELLED: Final = "CANCELLED"
# Queued-pod status after its `expiresAt` passed without a deploy.
QUEUED_POD_EXPIRED: Final = "EXPIRED"
# Queued-pod status when Runpod could not deploy it.
QUEUED_POD_FAILED: Final = "FAILED"

# Wire name of the id field of every Runpod object.
ID_FIELD: Final = "id"
# Name of the worker template the cloud setup maintains (the only place naming the image); it
# carries the contracts major, so app versions of different majors keep their own templates.
TEMPLATE_NAME: Final = f"transcribe-ui-worker-schema-{SCHEMA_MAJOR}"
# Worker image repository on Docker Hub (architecture §5.1).
WORKER_IMAGE_REPOSITORY: Final = "markfazekasgh/transcribe-ui-worker"
# Worker image the template pins: the tag of the contracts major the app was built with.
WORKER_IMAGE: Final = f"{WORKER_IMAGE_REPOSITORY}:schema-{SCHEMA_MAJOR}"
# Name of the Global Volume holding the worker's result backups.
GLOBAL_VOLUME_NAME: Final = "transcribe-ui-backups"

# Worker env variable -> Runpod secret holding its value, in the order of `R2Credentials`
# fields (variable names of `transcribe_worker.pipeline.constants`; Runpod refuses secret names
# starting with RUNPOD).
SECRET_FOR_ENV: Final = (
    ("R2_ENDPOINT_URL", "transcribe_ui_r2_endpoint_url"),
    ("R2_BUCKET", "transcribe_ui_r2_bucket"),
    ("R2_ACCESS_KEY_ID", "transcribe_ui_r2_access_key_id"),
    ("R2_SECRET_ACCESS_KEY", "transcribe_ui_r2_secret_access_key"),
)
# Template env value referencing a Runpod secret; Runpod fills it in when the pod starts.
ENV_REFERENCE_FORMAT: Final = "{{{{ RUNPOD_SECRET_{name} }}}}"

# Error code: Runpod refused the API key (HTTP 401).
ERROR_RUNPOD_KEY_REJECTED: Final = "runpod_key_rejected"
# Error code: the API key lacks a scope the call needs (HTTP 403; details name the scope).
ERROR_RUNPOD_KEY_SCOPE: Final = "runpod_key_missing_scope"
# Error code: Runpod could not be reached or answered unexpectedly; nothing more was done.
ERROR_CLOUD_UNREACHABLE: Final = "cloud_unreachable"
# Error code: a create may or may not have taken effect; running `setup_cloud` again adopts it.
ERROR_CLOUD_SETUP_INTERRUPTED: Final = "cloud_setup_interrupted"
# Error code: several Global Volumes carry the app's name; they hold backups, so the user picks.
ERROR_CLOUD_VOLUME_DUPLICATED: Final = "cloud_volume_duplicated"
# Error code: a duplicate cloud object could not be removed because it is in use.
ERROR_CLOUD_OBJECT_IN_USE: Final = "cloud_object_in_use"
# Error code: a cloud object `launch_worker` needs does not exist (run `setup_cloud`).
ERROR_CLOUD_NOT_SET_UP: Final = "cloud_not_set_up"
# Error code: a cloud object is outdated or duplicated (run `setup_cloud`).
ERROR_CLOUD_SETUP_OUTDATED: Final = "cloud_setup_outdated"
