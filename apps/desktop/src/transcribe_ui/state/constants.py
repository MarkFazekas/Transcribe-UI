"""Constants of the local state: paths, database, locks, settings and logging."""

import errno
import logging
from types import MappingProxyType
from typing import Final

# Application name for the per-user directories (platformdirs `appname`).
APP_DIR_NAME: Final = "Transcribe-UI"
# Sub-folders of an injected root (tests): data, config, cache and log dirs.
ROOT_DATA_FOLDER: Final = "data"
# Config sub-folder of an injected root.
ROOT_CONFIG_FOLDER: Final = "config"
# Cache sub-folder of an injected root.
ROOT_CACHE_FOLDER: Final = "cache"
# Log sub-folder of an injected root.
ROOT_LOG_FOLDER: Final = "log"

# File name of the SQLite database in the user data dir.
DATABASE_FILE_NAME: Final = "state.sqlite3"
# File name of the writer lock next to the database (held while a process uses it).
WRITER_LOCK_FILE_NAME: Final = "state.lock"
# Permission bits of the lock file when it is created (POSIX).
WRITER_LOCK_FILE_MODE: Final = 0o600
# errno values of a failed lock meaning "another process holds it" (POSIX flock, Windows `_locking`).
WRITER_LOCK_HELD_ERRNOS: Final = frozenset((errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, errno.EDEADLK))
# Constraint names (Alembic batch migrations on SQLite find constraints by name).
CONSTRAINT_NAMING_CONVENTION: Final = MappingProxyType(
    {
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    },
)
# Foreign key action: rows of a deleted batch, item or job go with it.
CASCADE: Final = "CASCADE"
# Length of a batch id column (`batch_YYYYMMDD_HHmmss` plus a collision suffix, with room).
BATCH_ID_LENGTH: Final = 40
# Length of a ULID column.
ULID_COLUMN_LENGTH: Final = 26
# Length of a language code column (ISO 639-1 or 639-3).
LANGUAGE_CODE_LENGTH: Final = 3
# Length of a phase, status, kind or outcome column.
STATUS_LENGTH: Final = 32
# Seconds a database transaction waits for another thread's write transaction (SQLite busy timeout).
DATABASE_BUSY_TIMEOUT_S: Final = 30.0
# Package holding the Alembic environment (`env.py`, `script.py.mako`, `versions/`).
MIGRATIONS_PACKAGE: Final = "transcribe_ui.state"
# Folder of the Alembic environment inside `MIGRATIONS_PACKAGE`.
MIGRATIONS_FOLDER: Final = "migrations"
# Alembic config attribute carrying the connection the migrations run on.
MIGRATION_CONNECTION_KEY: Final = "connection"
# Alembic target revision of a start-up upgrade.
HEAD_REVISION: Final = "head"

# Seconds the runs lock is waited for before the request gives `runs_busy`.
RUNS_LOCK_WAIT_S: Final = 30.0
# Seconds between cancel-token checks while the runs lock is waited for.
RUNS_LOCK_POLL_S: Final = 0.1

# File name of the settings JSON in the user config dir.
SETTINGS_FILE_NAME: Final = "settings.json"
# Prefix of the temp file a settings write goes through before `os.replace`.
SETTINGS_TEMP_PREFIX: Final = ".settings-"
# Attempts of the `os.replace` of a settings write (Windows refuses it while a reader has the file open).
SETTINGS_REPLACE_ATTEMPTS: Final = 5
# Seconds between those attempts.
SETTINGS_REPLACE_DELAY_S: Final = 0.05

# File name of the rotating log in the user log dir.
LOG_FILE_NAME: Final = "transcribe-ui.log"
# Size at which the log file is rotated.
LOG_MAX_BYTES: Final = 1024 * 1024
# Rotated log files kept next to the current one.
LOG_BACKUP_COUNT: Final = 3
# Format of a log line: human-readable, one record per line (tracebacks follow on their own lines).
LOG_FORMAT: Final = "%(asctime)s %(levelname)s %(name)s [%(threadName)s]: %(message)s"
# Level of the app's own loggers while logging to the file.
APP_LOG_LEVEL: Final = logging.INFO

# Error code: another process (the GUI, another CLI run) holds the database.
ERROR_APP_BUSY: Final = "app_busy"
# Error code: the database was written by a newer version of the app.
ERROR_DATABASE_TOO_NEW: Final = "database_too_new"
# Error code: the data dir, the lock file or the database cannot be opened.
ERROR_STATE_UNAVAILABLE: Final = "state_unavailable"
# Error code: another request is changing the same batch right now.
ERROR_BATCH_BUSY: Final = "batch_busy"
# Error code: another request holds the runs lock longer than the bounded wait.
ERROR_RUNS_BUSY: Final = "runs_busy"
# Error code: the batch is no longer in the phase the request expected; nothing was changed.
ERROR_PHASE_CONFLICT: Final = "phase_conflict"
# Error code: no batch with this id exists.
ERROR_BATCH_NOT_FOUND: Final = "batch_not_found"
# Error code: the settings file exists but cannot be read or is not valid.
ERROR_SETTINGS_INVALID: Final = "settings_invalid"

# Error code: the settings file cannot be written; the previous file is unchanged.
ERROR_SETTINGS_UNWRITABLE: Final = "settings_unwritable"

# Shape of a media file extension in the settings: without the dot, lower case.
MEDIA_EXTENSION_PATTERN: Final = r"^[a-z0-9]{1,10}$"
# Video extensions offered by a fresh installation.
DEFAULT_VIDEO_EXTENSIONS: Final = (
    "3gp",
    "avi",
    "flv",
    "m2ts",
    "m4v",
    "mkv",
    "mov",
    "mp4",
    "mpeg",
    "mpg",
    "mts",
    "ogv",
    "ts",
    "vob",
    "webm",
    "wmv",
)
# Audio extensions offered by a fresh installation.
DEFAULT_AUDIO_EXTENSIONS: Final = (
    "aac",
    "ac3",
    "aiff",
    "flac",
    "m4a",
    "mka",
    "mp3",
    "ogg",
    "opus",
    "wav",
    "wma",
)
# Folders closest to the file joined into the name by the flat-with-folders placement (X, §3.5).
DEFAULT_PLACEMENT_FOLDER_COUNT: Final = 1
