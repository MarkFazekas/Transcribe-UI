"""Per-user directories of the app (platformdirs), or folders under an injected root in tests."""

from dataclasses import dataclass
from pathlib import Path
from typing import Self

from platformdirs import PlatformDirs

from transcribe_ui.state.constants import (
    APP_DIR_NAME,
    DATABASE_FILE_NAME,
    LOG_FILE_NAME,
    ROOT_CACHE_FOLDER,
    ROOT_CONFIG_FOLDER,
    ROOT_DATA_FOLDER,
    ROOT_LOG_FOLDER,
    SETTINGS_FILE_NAME,
    WRITER_LOCK_FILE_NAME,
)


@dataclass(frozen=True)
class AppPaths:
    """Where the app keeps its data (database), config (settings), cache (temp audio) and logs.

    Nothing is created here; each user creates its folder when it first writes.
    """

    data_dir: Path
    config_dir: Path
    cache_dir: Path
    log_dir: Path

    @classmethod
    def for_user(cls) -> Self:
        """Return the current user's directories (`%LOCALAPPDATA%`, `~/Library`, XDG)."""
        dirs = PlatformDirs(APP_DIR_NAME, appauthor=False)
        return cls(
            data_dir=dirs.user_data_path,
            config_dir=dirs.user_config_path,
            cache_dir=dirs.user_cache_path,
            log_dir=dirs.user_log_path,
        )

    @classmethod
    def under(cls, root: Path) -> Self:
        """Return the four directories as sub-folders of one root (tests, portable runs)."""
        return cls(
            data_dir=root / ROOT_DATA_FOLDER,
            config_dir=root / ROOT_CONFIG_FOLDER,
            cache_dir=root / ROOT_CACHE_FOLDER,
            log_dir=root / ROOT_LOG_FOLDER,
        )

    @property
    def database_file(self) -> Path:
        """Return the SQLite database file."""
        return self.data_dir / DATABASE_FILE_NAME

    @property
    def writer_lock_file(self) -> Path:
        """Return the lock file that only one process at a time holds."""
        return self.data_dir / WRITER_LOCK_FILE_NAME

    @property
    def settings_file(self) -> Path:
        """Return the settings JSON file."""
        return self.config_dir / SETTINGS_FILE_NAME

    @property
    def log_file(self) -> Path:
        """Return the current rotating log file."""
        return self.log_dir / LOG_FILE_NAME
