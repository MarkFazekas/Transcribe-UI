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
# Mask turning an extended SQLite result code into its primary code.
SQLITE_PRIMARY_CODE_MASK: Final = 0xFF
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
# Suffix of that temp file.
SETTINGS_TEMP_SUFFIX: Final = ".json"
# Age in seconds after which a leftover settings temp file (from a crashed write) is removed.
SETTINGS_STALE_TEMP_S: Final = 60.0
# Pauses between attempts of a settings read or replace while Windows reports a sharing
# violation (another process has the file open); about 2 s in all.
SETTINGS_SHARING_RETRY_DELAYS_S: Final = (0.05, 0.1, 0.2, 0.4, 0.6, 0.65)

# Suffix the log file gets while it is being rotated (renamed first, then moved to `.1`).
LOG_ROTATING_SUFFIX: Final = ".rotating"
# Bytes (64 kibibytes) appended after a failed rotation before the next attempt (another process held the file).
LOG_ROLLOVER_RETRY_BYTES: Final = 65536
# Encoding of the log files.
LOG_ENCODING: Final = "utf-8"
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
# Error code: the database stayed locked by another transaction past the busy timeout; re-send.
ERROR_STATE_BUSY: Final = "state_busy"
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

# Placement strategy offered by a fresh installation (§3.5).
DEFAULT_PLACEMENT_STRATEGY: Final = "next_to_media"
# Folders closest to the file joined into the name by the flat-with-folders placement (X, §3.5).
DEFAULT_PLACEMENT_FOLDER_COUNT: Final = 1
