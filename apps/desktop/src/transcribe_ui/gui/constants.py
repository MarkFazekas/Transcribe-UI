"""Constants of the GUI frontend (architecture §3.1 "GUI")."""

import logging
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

from transcribe_ui.constants import DISTRIBUTION_NAME
from transcribe_ui.credentials.constants import ERROR_CREDENTIALS_INVALID, ERROR_CREDENTIALS_MISSING
from transcribe_ui.runpod.constants import ERROR_CLOUD_NOT_SET_UP, ERROR_CLOUD_SETUP_OUTDATED
from transcribe_ui.runs.constants import ERROR_CLEANUP_PENDING
from transcribe_ui.state.constants import ERROR_APP_BUSY

# Name of a thread pool of the GUI's request bridge.
type PoolName = Literal["scan", "long", "short", "poll"]
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

# The one key of a single-flight channel (the poll has one tick at a time).
POLL_KEY: Final = "poll"

# Command line the `QApplication` is created with (its application name).
QT_APPLICATION_ARGUMENTS: Final = (DISTRIBUTION_NAME,)
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

# Pool of folder scans, so a new file selection never waits behind uploads or downloads (Q33).
SCAN_POOL: Final[PoolName] = "scan"
# Pool of long requests (review, conversion and upload, downloads, cleanup, cloud setup).
LONG_POOL: Final[PoolName] = "long"
# Pool of every other user-started request, so Stop and lists never wait behind an upload.
SHORT_POOL: Final[PoolName] = "short"
# Pool of the start-up checks, the poll and "Refresh now" (one reserved thread).
POLL_POOL: Final[PoolName] = "poll"
# Threads of the scan pool: one (the selection sends scans on a `latest` channel, so a new one replaces the last).
SCAN_POOL_THREADS: Final = 1
# Threads of the long pool.
LONG_POOL_THREADS: Final = 3
# Threads of the short pool.
SHORT_POOL_THREADS: Final = 3
# Threads of the poll pool: one, reserved, so ticks queue only behind a start-up check or Refresh now.
POLL_POOL_THREADS: Final = 1
# Threads of each pool.
POOL_THREADS: Final[Mapping[PoolName, int]] = MappingProxyType(
    {
        SCAN_POOL: SCAN_POOL_THREADS,
        LONG_POOL: LONG_POOL_THREADS,
        SHORT_POOL: SHORT_POOL_THREADS,
        POLL_POOL: POLL_POOL_THREADS,
    },
)
# Pool of each request action that does not run on the short pool (presentation of threading, not a business rule).
ACTION_POOLS: Final[Mapping[str, PoolName]] = MappingProxyType(
    {
        "scan_tree": SCAN_POOL,
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
# How long the app waits for running requests after the event loop ended before ending the process, in milliseconds.
SHUTDOWN_WAIT_MS: Final = 15_000

# Environment variable naming the Qt platform plugin(s), `;`-separated, each with optional `:args`.
QT_PLATFORM_VARIABLE: Final = "QT_QPA_PLATFORM"
# Environment variable naming the X11 display.
X11_DISPLAY_VARIABLE: Final = "DISPLAY"
# Environment variable naming the Wayland display.
WAYLAND_DISPLAY_VARIABLE: Final = "WAYLAND_DISPLAY"
# Environment variable handing a Wayland client an already connected socket.
WAYLAND_SOCKET_VARIABLE: Final = "WAYLAND_SOCKET"
# Environment variable naming the folder of the Wayland sockets.
RUNTIME_DIR_VARIABLE: Final = "XDG_RUNTIME_DIR"
# Socket libwayland connects to when WAYLAND_DISPLAY is not set (in XDG_RUNTIME_DIR).
DEFAULT_WAYLAND_SOCKET: Final = "wayland-0"
# Environment variables of which the default Qt platform (xcb or Wayland) needs one, on non-macOS POSIX systems.
DISPLAY_VARIABLES: Final = (X11_DISPLAY_VARIABLE, WAYLAND_DISPLAY_VARIABLE)
# Qt platform plugin of X11, which needs `DISPLAY`.
X11_PLATFORM: Final = "xcb"
# Prefix of the Qt platform plugins of Wayland (`wayland`, `wayland-egl`, ...), which need `WAYLAND_DISPLAY`.
WAYLAND_PLATFORM_PREFIX: Final = "wayland"
# Qt platform plugins that need neither a display server nor a device, so they always start.
HEADLESS_PLATFORMS: Final = frozenset(("offscreen", "minimal", "vnc"))
# `sys.platform` values that always have a display for Qt (Windows, macOS).
DESKTOP_PLATFORMS: Final = frozenset(("win32", "darwin"))
# Message of `display_unavailable` when QT_QPA_PLATFORM is not set and no display variable is.
NO_DISPLAY_MESSAGE: Final = (
    "No display is available for the GUI: neither DISPLAY nor WAYLAND_DISPLAY is set. "
    "Use the `transcribe-ui` command line instead."
)
# Message of `display_unavailable` when no platform QT_QPA_PLATFORM names can start (`{platforms}`: its value).
FORCED_PLATFORM_MESSAGE: Final = (
    "No display is available for the GUI: QT_QPA_PLATFORM={platforms} cannot start here "
    "(xcb needs DISPLAY, wayland needs WAYLAND_DISPLAY or a wayland-0 socket, device platforms are not supported). "
    "Use the `transcribe-ui` command line instead."
)

# Window title, the app's name (the app version is appended once `get_app_info` answered, M6.3).
WINDOW_TITLE: Final = "Transcribe-UI"
# Key of the "New job" page.
PAGE_NEW_JOB: Final = "new_job"
# Key of the "Jobs" page.
PAGE_JOBS: Final = "jobs"
# Key of the "Cloud" page.
PAGE_CLOUD: Final = "cloud"
# Key of the "Settings" page.
PAGE_SETTINGS: Final = "settings"
# The navigation's pages in order: key, whether the page needs the database (Q33); titles: `gui/texts.py`.
PAGES: Final = (
    (PAGE_NEW_JOB, True),
    (PAGE_JOBS, True),
    (PAGE_CLOUD, True),
    (PAGE_SETTINGS, False),
)
# Bus signal that tells the pages that batches changed (a tuple of batch ids; empty: any batch).
BATCHES_CHANGED_SIGNAL: Final = "batches_changed"
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
# Details key of `cleanup_pending` that lists the batches to clean up.
CLEANUP_PENDING_BATCHES_KEY: Final = "batch_ids"
# Environment variable that starts the GUI in its smoke mode: it closes itself once start-up answered (release builds).
SMOKE_TEST_VARIABLE: Final = "TRANSCRIBE_UI_SMOKE_TEST"
# Value of the smoke mode's variable that turns the mode on (any other value leaves the GUI as it is).
SMOKE_TEST_ON: Final = "1"
# How long the smoke mode waits for the start-up to answer before it fails, in milliseconds.
SMOKE_TEST_DEADLINE_MS: Final = 120_000
# Exit code of a smoke run in which a request failed, the start-up did not finish or the texts of Qt itself are missing.
SMOKE_TEST_FAILED_EXIT_CODE: Final = 1
# Prefix of the translation files of the texts Qt itself shows (standard buttons, context menus).
QT_TRANSLATION_PREFIX: Final = "qtbase_"
# Exit code of "Quit now".
QUIT_NOW_EXIT_CODE: Final = 0
# Package holding the window icon PNGs (package data, also in the one-file bundle).
ICON_PACKAGE: Final = "transcribe_ui.gui"
# Folder of the window icon PNGs inside that package.
ICON_FOLDER: Final = "icons"
# File name of an icon PNG; `{size}` is its width and height in pixels.
ICON_FILE_PATTERN: Final = "transcribe-ui-{size}.png"
# The pixel sizes of the bundled icon PNGs.
ICON_SIZES: Final = (16, 24, 32, 48, 64, 128, 256)
