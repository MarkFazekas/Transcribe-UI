"""Domain errors of the local state; the backend turns them into error responses (§6)."""

from transcribe_ui.api.errors import DomainError
from transcribe_ui.state.constants import (
    ERROR_APP_BUSY,
    ERROR_BATCH_BUSY,
    ERROR_BATCH_NOT_FOUND,
    ERROR_DATABASE_TOO_NEW,
    ERROR_PHASE_CONFLICT,
    ERROR_RUNS_BUSY,
    ERROR_SETTINGS_INVALID,
    ERROR_SETTINGS_UNWRITABLE,
    ERROR_STATE_UNAVAILABLE,
)


class AppBusyError(DomainError):
    """Another process holds the database; requests that need no database still work."""

    def __init__(self) -> None:
        """Use the fixed `app_busy` code and message."""
        super().__init__(
            ERROR_APP_BUSY,
            "Transcribe-UI is already running in another window or process; close it and try again.",
        )


class DatabaseTooNewError(DomainError):
    """The database was migrated by a newer app version; this one must not touch it."""

    def __init__(self, revisions: tuple[str, ...]) -> None:
        """Name the unknown revisions in the details."""
        super().__init__(
            ERROR_DATABASE_TOO_NEW,
            "The local database was written by a newer version of Transcribe-UI; update the app.",
            {"revisions": list(revisions)},
        )


class StateUnavailableError(DomainError):
    """The data dir, the lock file or the database cannot be opened (the log has the reason)."""

    def __init__(self) -> None:
        """Use the fixed `state_unavailable` code and message."""
        super().__init__(
            ERROR_STATE_UNAVAILABLE,
            "The local database cannot be opened; the details are in the log.",
        )


class BatchBusyError(DomainError):
    """Another request is changing the batch; batch locks are never waited for."""

    def __init__(self, batch_id: str) -> None:
        """Name the batch in the details."""
        super().__init__(
            ERROR_BATCH_BUSY,
            "Another request is working on this batch; try again when it has finished.",
            {"batch_id": batch_id},
        )


class RunsBusyError(DomainError):
    """The runs lock stayed held for the whole bounded wait; re-sending the request resumes."""

    def __init__(self) -> None:
        """Use the fixed `runs_busy` code and message."""
        super().__init__(
            ERROR_RUNS_BUSY,
            "Another request is working on the cloud runs; send the request again to resume.",
        )


class BatchNotFoundError(DomainError):
    """No batch with the id exists."""

    def __init__(self, batch_id: str) -> None:
        """Name the batch in the details."""
        super().__init__(ERROR_BATCH_NOT_FOUND, "There is no batch with this id.", {"batch_id": batch_id})


class PhaseConflictError(DomainError):
    """A compare-and-set phase change lost a race (or came too late); nothing was changed."""

    def __init__(self, batch_id: str, expected: str, actual: str) -> None:
        """Name the batch and both phases in the details."""
        super().__init__(
            ERROR_PHASE_CONFLICT,
            "The batch has moved on in the meantime; nothing was changed. Reload it and try again.",
            {"batch_id": batch_id, "expected": expected, "actual": actual},
        )


class SettingsInvalidError(DomainError):
    """The settings file cannot be read or is not valid; `update_settings` replaces it."""

    def __init__(self) -> None:
        """Use the fixed `settings_invalid` code and message."""
        super().__init__(
            ERROR_SETTINGS_INVALID,
            "The settings file cannot be read or is not valid; save the settings again to replace it.",
        )


class SettingsUnwritableError(DomainError):
    """The settings file cannot be written (the log has the reason); the old file is unchanged."""

    def __init__(self) -> None:
        """Use the fixed `settings_unwritable` code and message."""
        super().__init__(
            ERROR_SETTINGS_UNWRITABLE,
            "The settings cannot be saved; the details are in the log.",
        )
