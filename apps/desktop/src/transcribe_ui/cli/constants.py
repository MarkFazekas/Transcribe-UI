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

# Log file of the CLI in the user log dir; each frontend has its own, so no two frontends rotate one file.
CLI_LOG_FILE_NAME: Final = "transcribe-ui-cli.log"

# Line end of every line the CLI writes, on every OS.
LINE_END: Final = b"\n"

# Error codes answered with EXIT_INVALID_REQUEST: the input was refused before anything ran.
INVALID_INPUT_CODES: Final = frozenset((ERROR_INVALID_REQUEST, ERROR_REQUEST_UNREADABLE, ERROR_INVALID_ARGUMENTS))

# Byte order mark of a UTF-8 file (Windows editors write it); stripped before parsing.
UTF8_BOM: Final = b"\xef\xbb\xbf"
# Byte order marks of UTF-16 files (PowerShell 5.1 `>` and `Out-File`; UTF-32 LE starts the same).
UTF16_BOMS: Final = (b"\xff\xfe", b"\xfe\xff")
# Message of the refusal of a request file that is not UTF-8.
NOT_UTF8_MESSAGE: Final = "The request file must be UTF-8 (it starts with a UTF-16 byte order mark)."
