"""Constants of the command-line frontend."""

from typing import Final, Literal

from transcribe_ui.api.constants import ERROR_INVALID_REQUEST
from transcribe_ui.credentials.constants import R2_BUCKET, R2_ENDPOINT_URL

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
# Message of the refusal of a request that is not UTF-8 (a file or stdin).
NOT_UTF8_MESSAGE: Final = "The request must be UTF-8 (it starts with a UTF-16 byte order mark)."

# Argument of `run` that reads the request from stdin instead of a file (`./-` names a file `-`).
STDIN_ARGUMENT: Final = "-"

# How `run` writes progress events to stderr: one JSON line or one human-readable line per event.
type ProgressFormat = Literal["ndjson", "text"]
# The choices of `run --progress`, the default first.
PROGRESS_FORMATS: Final[tuple[ProgressFormat, ...]] = ("ndjson", "text")
# Format of the progress events when `--progress` is not given (scripts read NDJSON).
DEFAULT_PROGRESS_FORMAT: Final[ProgressFormat] = "ndjson"
# Separator of the path at the end of a text progress line.
TEXT_PROGRESS_PATH_SEPARATOR: Final = ": "

# Answers that mean the end of input: Ctrl+Z and Ctrl+D, which Windows `getpass` returns as text.
END_OF_INPUT_ANSWERS: Final = frozenset(("\x1a", "\x04"))
# The answer of a line (echoed or hidden) that is not text in the terminal's encoding: the replacement
# character, which no credential accepts, so the field's rule is shown and it is asked again.
NOT_TEXT_ANSWER: Final = "\ufffd"
# Credentials `credentials` asks for with echo (not secret); the keys and the secret stay hidden.
ECHOED_CREDENTIALS: Final = frozenset((R2_ENDPOINT_URL, R2_BUCKET))
# Action of the request the `credentials` command builds from its prompts.
STORE_CREDENTIALS_ACTION: Final = "store_credentials"
# Message of the refusal of `credentials` when stdin is not a terminal (the answers would be echoed).
NOT_A_TERMINAL_MESSAGE: Final = (
    "The credentials command needs a terminal on stdin; store credentials with a request file"
    " (`run <request.json>`) instead."
)
# Message of the refusal of a command line without a command.
NO_COMMAND_MESSAGE: Final = "A command is required (run, schema or credentials)."
