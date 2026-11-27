"""Constants of the backend façade."""

from typing import Final

# Error code: the request document is not valid JSON or does not match the `Request` union.
ERROR_INVALID_REQUEST: Final = "invalid_request"
# Error code: the request was cancelled; what was done is persisted, re-sending resumes.
ERROR_CANCELLED: Final = "cancelled"
# Error code: an unexpected failure; the details are in the log, never in the response.
ERROR_INTERNAL: Final = "internal_error"
# Message of `internal_error`: generic, so no internals (or secrets) reach a response.
INTERNAL_ERROR_MESSAGE: Final = "Unexpected internal error; the details are in the log."

# Serialization context key of the function rewriting `LocalPath` values (golden-file tests).
LOCAL_PATH_CONTEXT_KEY: Final = "local_path"
# Serialization context key of the function rewriting `PathFlavor` values (golden-file tests).
PATH_FLAVOR_CONTEXT_KEY: Final = "path_flavor"
