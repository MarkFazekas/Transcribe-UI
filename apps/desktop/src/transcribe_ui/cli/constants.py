"""Constants of the command-line frontend."""

from typing import Final

from transcribe_ui.api.constants import ERROR_INVALID_REQUEST

# Exit code: the request succeeded.
EXIT_OK: Final = 0
# Exit code: the backend answered with an error response.
EXIT_ERROR: Final = 1
# Exit code: the request file (or the command line) is invalid; nothing was run.
EXIT_INVALID_REQUEST: Final = 2
# Exit code: the request needs a confirmation; nothing was changed.
EXIT_NEEDS_CONFIRMATION: Final = 3
# Exit code: cancelled with Ctrl+C (128 + SIGINT, the shell convention); what was done is persisted.
EXIT_CANCELLED: Final = 130

# Error code: the request file cannot be read.
ERROR_REQUEST_UNREADABLE: Final = "request_unreadable"
# Error code: the command line is not valid.
ERROR_INVALID_ARGUMENTS: Final = "invalid_arguments"

# Line end of every line the CLI writes, on every OS.
LINE_END: Final = b"\n"

# Error codes answered with EXIT_INVALID_REQUEST: the input was refused before anything ran.
INVALID_INPUT_CODES: Final = frozenset((ERROR_INVALID_REQUEST, ERROR_REQUEST_UNREADABLE, ERROR_INVALID_ARGUMENTS))
