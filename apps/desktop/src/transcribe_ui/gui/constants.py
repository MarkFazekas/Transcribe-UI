"""Constants of the GUI frontend (architecture §3.1 "GUI")."""

import logging
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

from transcribe_ui.credentials.constants import ERROR_CREDENTIALS_INVALID, ERROR_CREDENTIALS_MISSING
from transcribe_ui.runpod.constants import ERROR_CLOUD_NOT_SET_UP, ERROR_CLOUD_SETUP_OUTDATED
from transcribe_ui.runs.constants import ERROR_CLEANUP_PENDING
from transcribe_ui.state.constants import ERROR_APP_BUSY

# Name of a thread pool of the GUI's request bridge.
type PoolName = Literal["long", "short", "poll"]
# Where a call is: waiting in its pool, running on a pool thread, or completed.
type CallState = Literal["queued", "running", "done"]
# Where an activity of the panel is.
type ActivityState = Literal["queued", "running", "stopped", "failed", "finished"]
# A fix the error dialog offers for an error code (presentation; the backend decides).
type FixKind = Literal["accounts", "cloud_setup", "open_batch", "retry"]

# State of a call waiting in its pool.
CALL_QUEUED: Final[CallState] = "queued"
# State of a call running on a pool thread.
CALL_RUNNING: Final[CallState] = "running"
# State of a completed call.
CALL_DONE: Final[CallState] = "done"

# Log file of the GUI in the user log dir; each frontend has its own, so no two frontends rotate one file.
GUI_LOG_FILE_NAME: Final = "transcribe-ui-gui.log"
# Module of the GUI's start (`run_gui`), imported by `gui/main.py` only after the display check.
GUI_APP_MODULE: Final = "transcribe_ui.gui.app"
# Logger that receives the messages of Qt (warnings, platform plugin notes).
QT_LOGGER_NAME: Final = "transcribe_ui.gui.qt"
# Log level of each Qt message type, by the `QtMsgType` member's name (others log as warnings).
QT_LOG_LEVELS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "QtDebugMsg": logging.DEBUG,
        "QtInfoMsg": logging.INFO,
        "QtWarningMsg": logging.WARNING,
        "QtCriticalMsg": logging.ERROR,
        "QtFatalMsg": logging.CRITICAL,
    },
)

# Pool of long requests (scans, conversion and upload, downloads, cleanup, cloud setup).
LONG_POOL: Final[PoolName] = "long"
# Pool of every other user-started request, so Stop and lists never wait behind an upload.
SHORT_POOL: Final[PoolName] = "short"
# Pool of the start-up checks, the poll and "Refresh now" (one reserved thread).
POLL_POOL: Final[PoolName] = "poll"
# Threads of the long pool.
LONG_POOL_THREADS: Final = 3
# Threads of the short pool.
SHORT_POOL_THREADS: Final = 3
# Threads of the poll pool: one, reserved, so ticks queue only behind a start-up check or Refresh now.
POLL_POOL_THREADS: Final = 1
# Threads of each pool.
POOL_THREADS: Final[Mapping[PoolName, int]] = MappingProxyType(
    {LONG_POOL: LONG_POOL_THREADS, SHORT_POOL: SHORT_POOL_THREADS, POLL_POOL: POLL_POOL_THREADS}
)
# Pool of each request action that does not run on the short pool (presentation of threading, not a business rule).
ACTION_POOLS: Final[Mapping[str, PoolName]] = MappingProxyType(
    {
        "scan_tree": LONG_POOL,
        "review_batch": LONG_POOL,
        "run_upload": LONG_POOL,
        "run_download": LONG_POOL,
        "import_custom_plan": LONG_POOL,
        "create_retry_batch": LONG_POOL,
        "cleanup_cloud": LONG_POOL,
        "setup_cloud": LONG_POOL,
        "refresh_status": POLL_POOL,
        "reconcile_cloud": POLL_POOL,
    },
)
# Pool of the actions `ACTION_POOLS` does not list.
DEFAULT_POOL: Final[PoolName] = SHORT_POOL

# Least time between two repaints of a view that shows progress events, in milliseconds.
PROGRESS_REPAINT_MS: Final = 100
# Interval at which a test or the close dialog checks whether every call has completed, in milliseconds.
IDLE_CHECK_MS: Final = 50

# Environment variable naming the Qt platform plugin(s), `;`-separated, each with optional `:args`.
QT_PLATFORM_VARIABLE: Final = "QT_QPA_PLATFORM"
# Environment variables of which at least one names a display server on Linux and other non-macOS POSIX systems.
DISPLAY_VARIABLES: Final = ("DISPLAY", "WAYLAND_DISPLAY")
# Qt platform plugins that need no display server (a platform list naming one of them always starts).
HEADLESS_PLATFORMS: Final = frozenset(("offscreen", "minimal", "minimalegl", "eglfs", "linuxfb", "vnc", "vkkhrdisplay"))
# `sys.platform` values that always have a display for Qt (Windows, macOS).
DESKTOP_PLATFORMS: Final = frozenset(("win32", "darwin"))
# What the GUI executable logs (and writes to stderr, if it has one) when no display is available.
DISPLAY_UNAVAILABLE_MESSAGE: Final = (
    "No display is available for the GUI (neither DISPLAY nor WAYLAND_DISPLAY is set); "
    "use the `transcribe-ui` command line instead."
)

# Window title (the app version is appended once `get_app_info` answered, M6.3).
WINDOW_TITLE: Final = "Transcribe-UI"
# Key of the "New job" page.
PAGE_NEW_JOB: Final = "new_job"
# Key of the "Jobs" page.
PAGE_JOBS: Final = "jobs"
# Key of the "Cloud" page.
PAGE_CLOUD: Final = "cloud"
# Key of the "Settings" page.
PAGE_SETTINGS: Final = "settings"
# The navigation's pages in order: key, title, whether the page needs the database (Q33).
PAGES: Final = (
    (PAGE_NEW_JOB, "New job", True),
    (PAGE_JOBS, "Jobs", True),
    (PAGE_CLOUD, "Cloud", True),
    (PAGE_SETTINGS, "Settings", False),
)
# Text of a page whose part is not built yet.
PLACEHOLDER_TEXT: Final = "This page is not available yet."
# Status bar text of an action whose handler is not connected yet.
NOT_AVAILABLE_TEXT: Final = "Not available yet."
# How long a status bar message stays, in milliseconds.
STATUS_MESSAGE_MS: Final = 5000

# Settings section of the accounts (credentials) page.
SECTION_ACCOUNTS: Final = "accounts"
# Settings section of the cloud setup page.
SECTION_CLOUD_SETUP: Final = "cloud_setup"
# Error codes that have a fix in the GUI, and the fix (any other code renders with the generic layout).
ERROR_FIXES: Final[Mapping[str, FixKind]] = MappingProxyType(
    {
        ERROR_CREDENTIALS_MISSING: "accounts",
        ERROR_CREDENTIALS_INVALID: "accounts",
        ERROR_CLOUD_NOT_SET_UP: "cloud_setup",
        ERROR_CLOUD_SETUP_OUTDATED: "cloud_setup",
        ERROR_CLEANUP_PENDING: "open_batch",
        ERROR_APP_BUSY: "retry",
    },
)
# Label of each fix's button.
FIX_LABELS: Final[Mapping[FixKind, str]] = MappingProxyType(
    {
        "accounts": "Open accounts",
        "cloud_setup": "Open cloud setup",
        "open_batch": "Open the job",
        "retry": "Retry",
    },
)
# Details key of `cleanup_pending` that lists the batches to clean up.
CLEANUP_PENDING_BATCHES_KEY: Final = "batch_ids"
# Header of the key column of an error's or a confirmation's details table.
DETAIL_KEY_HEADER: Final = "Detail"
# Header of the value column of an error's or a confirmation's details table.
DETAIL_VALUE_HEADER: Final = "Value"
# Note added to `internal_error`.
SEE_LOG_TEXT: Final = "See the log for what went wrong."

# Status text of an activity by state (a running one shows its progress, a failed one its error instead).
ACTIVITY_STATE_TEXTS: Final[Mapping[ActivityState, str]] = MappingProxyType(
    {
        "queued": "Waiting to start",
        "running": "Running",
        # Shown neutral, never as a failure.
        "stopped": "Stopped; continue any time.",
        "failed": "Failed",
        "finished": "Finished",
    },
)

# Title of the writer-lock notice.
APP_BUSY_TITLE: Final = "Transcribe-UI is busy in another window"
# Text of the writer-lock notice (the message of the backend follows it).
APP_BUSY_TEXT: Final = (
    "Another Transcribe-UI (a second window or a command-line run) is using the job database. "
    "Settings, accounts and the cloud setup stay usable; close the other one, then Retry."
)

# Close question when calls are queued or running or a run is not final (Q30).
CLOSE_QUESTION: Final = "Stop and close? The work continues from where it stopped the next time you start it."
# Note added to the close question while a run is not final (Q30).
CLOSE_CLOUD_NOTE: Final = (
    "The cloud machine keeps working without the app: it stops itself when the work is done or at its "
    "time limit, and the app shows its results the next time it starts."
)
# Text of the dialog shown while running calls finish their current step.
FINISHING_TEXT: Final = "Finishing the current step..."
# Exit code of "Quit now".
QUIT_NOW_EXIT_CODE: Final = 0
